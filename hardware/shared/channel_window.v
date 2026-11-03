`timescale 1ns/1ps
// Full-frame C16 gather into a virtual halo window, channel-sliced in DDR.
// C16 HWC activations; O,KY,KX,C16 weights. Load <=32 channels per pass.
// Bounded full-channel input/weight caches feed the unchanged sliced MAC.
// Cache-to-local copy overlaps synchronous read and prior write; final word drains before MAC launch.
// Reuse is explicitly authorized by the enclosing layer, never by address alone.
// Virtual off-image pixels are literal signed zero; no out-of-range DDR read.
module channel_window #(parameter WATCHDOG=100000)(
 input clk,reset,cmd_valid,output cmd_ready,
 input cfg_reuse_input,cfg_reuse_weight,
 input [15:0] cfg_h,cfg_w,cfg_ci,cfg_co,input [1:0] cfg_k,cfg_s,cfg_p,
 input [15:0] cfg_full_h,cfg_full_w,
 input signed [16:0] cfg_origin_y,cfg_origin_x,
 input [29:0] cfg_activation,cfg_weight,cfg_bias,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready,
 output out_valid,input out_ready,output [255:0] out_result,output [7:0] out_mask,
 output [15:0] out_y,out_x,out_channel,
 output reg done,failed,output reg [7:0] error_code,
 output reg [31:0] batches,read_bytes,output [31:0] partial_vectors,final_vectors
);
 localparam IDLE=0,GEOM=1,SIZES=2,CHECK=3,BIAS=4,BIAS_WAIT=5,INPUT=6,INPUT_WAIT=7,WEIGHTS=8,WEIGHTS_WAIT=9,LAUNCH=10,RUN=11,NEXT=12,ZERO_BIAS=13,FINISH=14,SCAN=15,ZERO_INPUT=16,SCAN_ADV=17,ADDR_PIXEL=18,ADDR_SCALE=19,ADDR_BASE=20;
 localparam I_PREP=21,I_SCAN=22,I_PIXEL=23,I_SCALE=24,I_BASE=25,I_READ=26,I_WAIT=27,I_ADV=28,I_ZERO=29,
            W_PREP=30,W_READ=31,W_WAIT=32,COPY_I=33,COPY_W=34,COPY_STREAM=35,COPY_DRAIN=36;
 reg [5:0] state;
 reg [15:0] fh,fw,iy,ix,pixel_row,pixel_index;reg [16:0] source_pixels;reg [25:0] pixel_offset;reg signed [16:0] origin_y,origin_x;
 reg [11:0] input_offset;reg [1:0] zero_input_word;
 reg [5:0] read_pixels;
 wire signed [17:0] gy=$signed(origin_y)+$signed({1'b0,iy});
 wire signed [17:0] gx=$signed(origin_x)+$signed({1'b0,ix});
 wire inside_image=gy>=0&&gx>=0&&gy<$signed({1'b0,fh})&&gx<$signed({1'b0,fw});
 reg [29:0] pixel_base;reg [15:0] h,w,ci,co,oh,ow,cb,chunk;
 reg [1:0] k,s,p;reg [29:0] ab,wb,bb;
 reg [31:0] pixels,out_pixels,input_bytes,weight_bytes,source_bytes,source_weight_bytes,psum_words;
 reg [15:0] full_stride,local_stride,weight_rows,bias_bytes;reg [3:0] zero_word;
 assign cmd_ready=state==IDLE&&!failed;
 wire dr,dd,df,dwe;wire [7:0] de;wire [31:0] db;wire [12:0] da;wire [127:0] ddata;
 reg input_cached,weight_cached,input_valid,weight_valid,input_hit,weight_hit,weight_flat;
 reg [31:0] input_cache_bytes;reg [15:0] input_row_bytes,weight_row_bytes;
 reg [12:0] cache_offset,copy_source;reg [11:0] copy_dest;
 reg [5:0] cache_zero_word,channel_words,copy_pixel_step;
 reg copy_pending;reg [11:0] copy_pending_dest;
 reg copy_weights,copy_second;reg [15:0] copy_pixel,copy_count;
 reg [127:0] input_q,weight_cache_q;
 wire [127:0] copy_q=copy_weights ? weight_cache_q:input_q;
 reg [15:0] tag_h,tag_w,tag_fh,tag_fw,tag_ci,tag_wci,tag_wco;
 reg signed [16:0] tag_oy,tag_ox;reg [29:0] tag_ab,tag_wb;reg [1:0] tag_k;
 (* ram_style="block" *) reg [127:0] input_cache[0:4095];
 (* ram_style="block" *) reg [127:0] weight_cache[0:8191];
 wire issuing=state==BIAS||state==INPUT||state==WEIGHTS||state==I_READ||state==W_READ;
 wire [29:0] base=state==BIAS ? bb:state==I_READ ? pixel_base:state==W_READ ? wb:(state==INPUT ? pixel_base:wb)+cb;
 wire [29:0] stride=state==BIAS||state==I_READ ? 30'd0:state==W_READ ? {14'd0,weight_row_bytes}:{14'd0,full_stride};
 // Accepted CI<=32 means one complete, contiguous channel slice.
 reg contiguous_channels;reg two_words;
 wire [15:0] row_bytes=state==BIAS ? bias_bytes:state==I_READ ? input_row_bytes:state==W_READ ? (weight_flat ? source_weight_bytes[15:0]:weight_row_bytes):state==INPUT ?
 (two_words ? {5'd0,read_pixels,5'd0}:{6'd0,read_pixels,4'd0}):
 contiguous_channels ? weight_bytes[15:0]:local_stride;
 wire [15:0] rows=state==W_READ ? (weight_flat ? 16'd1:co):state==BIAS||state==INPUT||state==I_READ||contiguous_channels ? 16'd1:weight_rows;
 // Stage the complete descriptor before asserting cmd_valid: selection and DMA
 // product/validation no longer share a single state-to-enable timing path.
 reg dma_arm;reg [29:0] dma_base,dma_stride;reg [15:0] dma_row_bytes,dma_rows;
 always @(posedge clk)begin
 if(reset)begin dma_arm<=0;dma_base<=0;dma_stride<=0;dma_row_bytes<=0;dma_rows<=0;end
 else if(!issuing)dma_arm<=0;
 else if(!dma_arm)begin dma_base<=base;dma_stride<=stride;dma_row_bytes<=row_bytes;dma_rows<=rows;dma_arm<=1;end
 else if(dr)dma_arm<=0;
 end

 axi_tile_dma_guarded #(.WORD_ADDR_BITS(13),.WATCHDOG(WATCHDOG)) dma(.clk(clk),.reset(reset),.cmd_valid(issuing&&dma_arm),.cmd_ready(dr),.cmd_write(1'b0),
 .cmd_base(dma_base),.cmd_stride(dma_stride),.cmd_row_bytes(dma_row_bytes),.cmd_rows(dma_rows),.done(dd),.failed(df),.error_code(de),.transferred_bytes(db),
 .awready(1'b0),.wready(1'b0),.bresp(2'd0),.bvalid(1'b0),
 .araddr(araddr),.arlen(arlen),.arvalid(arvalid),.arready(arready),.rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid),.rready(rready),
 .mem_we(dwe),.mem_addr(da),.mem_wdata(ddata),.mem_rdata(128'd0));
 wire ce,cr,cd,cf,proto,cv;wire [11:0] ca;reg [127:0] cq;
 wire [511:0] result;wire [15:0] overflow,cx,cy,cc;
 wire ps_ready,ps_busy,ps_fail;reg conv_finished;reg [9:0] ordinal;
 wire [7:0] mask=8'hff>>(co-cc>=8 ? 0:8-(co-cc));
 conv_pipeline #(.DEPTHWISE(0)) conv(.clk(clk),.reset(reset),.cmd_valid(state==LAUNCH),.cmd_ready(cr),
 .cfg_h(h),.cfg_w(w),.cfg_ci(chunk),.cfg_co(co),.cfg_oh(oh),.cfg_ow(ow),.cfg_kernel(k),.cfg_stride(s),.cfg_pad(p),
 .cfg_activation(16'd0),.cfg_weight(16'hc000),.cfg_bias(16'hf800),.cfg_gaps(1'b0),
 .mem_addr(ca),.mem_en(ce),.mem_q(cq),.out_valid(cv),.out_ready(ps_ready&&state==RUN),.out_result(result),.out_overflow(overflow),
 .out_x(cx),.out_y(cy),.out_channel(cc),.done(cd),.failed(cf),.protocol_error(proto));
 wire [63:0] tag;
 psum_vector8 psum(.clk(clk),.reset(reset),.in_valid(cv&&state==RUN),.in_ready(ps_ready),.in_address(ordinal),
 .in_value(result[255:0]),.in_mask(mask),.in_first(cb==0),.in_last(cb+chunk==ci),.in_tag({16'd0,cc,cy,cx}),
 .out_valid(out_valid),.out_ready(out_ready),.out_value(out_result),.out_mask(out_mask),.out_tag(tag),.busy(ps_busy),.failed(ps_fail),.contributions(partial_vectors),.final_vectors(final_vectors));
 assign {out_channel,out_y,out_x}=tag[47:0];
 (* ram_style="block" *) reg [127:0] local_ram[0:4095];
 wire [11:0] write_address=state==ZERO_BIAS ? 12'hf80+zero_word:state==BIAS_WAIT ? 12'hf80+da:state==WEIGHTS_WAIT ? 12'hc00+da:state==ZERO_INPUT ? input_offset+zero_input_word:input_offset+da;
 wire local_write_zero=state==ZERO_BIAS||state==ZERO_INPUT;
 wire copy_write=(state==COPY_STREAM||state==COPY_DRAIN)&&copy_pending&&!reset;
 wire local_we=local_write_zero||copy_write||(dwe&&state!=I_WAIT&&state!=W_WAIT);
 wire [11:0] local_wa=copy_write ? (copy_weights ? 12'hc00:12'd0)+copy_pending_dest:write_address;
 wire [127:0] local_wd=local_write_zero ? 128'd0:copy_write ? copy_q:ddata;
 wire input_we=(dwe&&state==I_WAIT)||state==I_ZERO;
 wire [11:0] input_wa=state==I_ZERO ? cache_offset+cache_zero_word:cache_offset+da;
 wire [127:0] input_wd=state==I_ZERO ? 128'd0:ddata;
 always @(posedge clk)begin
 if(ce)cq<=local_ram[ca];
 if(local_we)local_ram[local_wa]<=local_wd;
 if(input_we)input_cache[input_wa]<=input_wd;
 if(dwe&&state==W_WAIT)weight_cache[da]<=ddata;
 if(state==COPY_STREAM&&!copy_weights)input_q<=input_cache[copy_source[11:0]];
 if(state==COPY_STREAM&&copy_weights)weight_cache_q<=weight_cache[copy_source];
 end
 wire [31:0] full_cp=((ci+15)>>4)<<4;
 wire [15:0] window_remaining=w-ix;
 wire [15:0] image_remaining=fw-gx[15:0];
 always @(posedge clk)begin
 if(reset)begin
 copy_pending<=0;copy_pending_dest<=0;input_cached<=0;weight_cached<=0;input_valid<=0;weight_valid<=0;input_hit<=0;weight_hit<=0;weight_flat<=0;
 input_cache_bytes<=0;input_row_bytes<=0;weight_row_bytes<=0;cache_offset<=0;cache_zero_word<=0;
 channel_words<=0;copy_pixel_step<=0;copy_source<=0;copy_dest<=0;copy_weights<=0;copy_second<=0;copy_pixel<=0;copy_count<=0;
 tag_h<=0;tag_w<=0;tag_fh<=0;tag_fw<=0;tag_ci<=0;tag_wci<=0;tag_wco<=0;tag_oy<=0;tag_ox<=0;tag_ab<=0;tag_wb<=0;tag_k<=0;
 end else begin
 copy_pending<=state==COPY_STREAM;
 if(state==COPY_STREAM)copy_pending_dest<=copy_dest;
 if(state==IDLE&&cmd_valid&&cmd_ready)begin
 input_hit<=cfg_reuse_input&&input_valid&&tag_h==cfg_h&&tag_w==cfg_w&&tag_fh==cfg_full_h&&tag_fw==cfg_full_w&&
 tag_ci==cfg_ci&&tag_oy==cfg_origin_y&&tag_ox==cfg_origin_x&&tag_ab==cfg_activation;
 weight_hit<=cfg_reuse_weight&&weight_valid&&tag_wci==cfg_ci&&tag_wco==cfg_co&&tag_wb==cfg_weight&&tag_k==cfg_k;
 // First group/tile of every layer explicitly invalidates matching old addresses.
 input_valid<=0;weight_valid<=0;
 tag_h<=cfg_h;tag_w<=cfg_w;tag_fh<=cfg_full_h;tag_fw<=cfg_full_w;tag_ci<=cfg_ci;
 tag_oy<=cfg_origin_y;tag_ox<=cfg_origin_x;tag_ab<=cfg_activation;
 tag_wci<=cfg_ci;tag_wco<=cfg_co;tag_wb<=cfg_weight;tag_k<=cfg_k;
 end
 if(state==SIZES)begin input_cache_bytes<=pixels*full_stride;weight_row_bytes<=(k==3 ? 9:1)*full_stride;end
 if(state==CHECK)begin input_cached<=ci>32&&input_cache_bytes<=65536;weight_cached<=source_weight_bytes<=131072;weight_flat<=source_weight_bytes<65536;
 channel_words<=full_stride>>4;copy_pixel_step<=(full_stride>>4)-(two_words ? 1:0);end
 if(state==I_PREP&&(input_hit||cb!=0))input_valid<=1;
 if(state==I_ADV&&ix+read_pixels==w&&iy+1==h)input_valid<=1;
 if(state==W_PREP&&(weight_hit||cb!=0))weight_valid<=1;
 if(state==W_WAIT&&dd&&!df)weight_valid<=1;
 if(state==FINISH&&failed)begin input_valid<=0;weight_valid<=0;end
 end
 if(reset)begin state<=IDLE;fh<=0;fw<=0;origin_y<=0;origin_x<=0;iy<=0;ix<=0;input_offset<=0;zero_input_word<=0;read_pixels<=1;contiguous_channels<=0;two_words<=0;pixel_base<=0;pixel_row<=0;pixel_index<=0;pixel_offset<=0;source_pixels<=0;h<=0;w<=0;ci<=0;co<=0;oh<=0;ow<=0;cb<=0;chunk<=0;k<=0;s<=0;p<=0;ab<=0;wb<=0;bb<=0;pixels<=0;out_pixels<=0;input_bytes<=0;weight_bytes<=0;source_bytes<=0;source_weight_bytes<=0;psum_words<=0;full_stride<=0;local_stride<=0;weight_rows<=0;bias_bytes<=0;zero_word<=0;done<=0;failed<=0;error_code<=0;batches<=0;read_bytes<=0;ordinal<=0;conv_finished<=0;end
 else begin done<=0;case(state)
 IDLE:if(cmd_valid&&cmd_ready)begin h<=cfg_h;w<=cfg_w;fh<=cfg_full_h;fw<=cfg_full_w;origin_y<=cfg_origin_y;origin_x<=cfg_origin_x;ci<=cfg_ci;co<=cfg_co;k<=cfg_k;s<=cfg_s;p<=cfg_p;ab<=cfg_activation;wb<=cfg_weight;bb<=cfg_bias;cb<=0;contiguous_channels<=cfg_ci!=0&&cfg_ci<=32;chunk<=cfg_ci>32 ? 32:cfg_ci;batches<=0;read_bytes<=0;error_code<=0;state<=GEOM;end
 GEOM:begin
 iy<=0;ix<=0;input_offset<=0;zero_input_word<=0;pixels<=h*w;source_pixels<=fh[8:0]*fw[8:0];oh<=((h+2*p-k)>> (s==2 ? 1:0))+1;ow<=((w+2*p-k)>>(s==2 ? 1:0))+1;
 two_words<=chunk>16;full_stride<=full_cp;local_stride<=((chunk+15)>>4)<<4;weight_rows<=co*(k==3 ? 9:1);bias_bytes<=((co+7)>>3)*32;state<=SIZES;
 end
 SIZES:begin input_bytes<=pixels*local_stride;weight_bytes<=weight_rows*local_stride;out_pixels<=oh*ow;source_bytes<=source_pixels*full_stride[9:0];source_weight_bytes<=weight_rows*full_stride[9:0];psum_words<=oh*ow*((co+7)>>3);state<=CHECK;end
 CHECK:if(fh==0||fw==0||fh>256||fw>256||p!=0||h==0||w==0||h>32||w>32||ci==0||ci>960||co==0||co>32||(k!=1&&k!=3)||(s!=1&&s!=2)||p>1||h+2*p<k||w+2*p<k||input_bytes>32768||weight_bytes>14336||out_pixels>1024||psum_words>1024||ab[3:0]!=0||wb[3:0]!=0||bb[3:0]!=0||{1'b0,ab}+source_bytes>31'h40000000||{1'b0,wb}+source_weight_bytes>31'h40000000||{1'b0,bb}+bias_bytes>31'h40000000)begin failed<=1;error_code<=6;state<=FINISH;end else state<=cb==0 ? BIAS:ZERO_BIAS;
 BIAS:if(dr&&dma_arm)state<=BIAS_WAIT;
 BIAS_WAIT:if(dd)begin if(df)begin failed<=1;error_code<=de;state<=FINISH;end else begin read_bytes<=read_bytes+db;state<=input_cached ? I_PREP:SCAN;end end
 INPUT:if(dr&&dma_arm)state<=INPUT_WAIT;
 INPUT_WAIT:if(dd)begin if(df)begin failed<=1;error_code<=de;state<=FINISH;end else begin read_bytes<=read_bytes+db;state<=SCAN_ADV;end end
 WEIGHTS:if(dr&&dma_arm)state<=WEIGHTS_WAIT;
 WEIGHTS_WAIT:if(dd)begin if(df)begin failed<=1;error_code<=de;state<=FINISH;end else begin read_bytes<=read_bytes+db;state<=LAUNCH;end end
 LAUNCH:if(cr)begin ordinal<=0;conv_finished<=0;state<=RUN;end
 RUN:begin
 if(cv&&ps_ready)begin ordinal<=ordinal+1;if(|overflow)begin failed<=1;error_code<=7;state<=FINISH;end end
 if(cd)conv_finished<=1;
 if(cf||proto||ps_fail)begin failed<=1;error_code<=7;state<=FINISH;end
 else if(conv_finished&&!ps_busy)begin batches<=batches+1;state<=NEXT;end
 end
 NEXT:if(cb+chunk==ci)state<=FINISH;
 else begin cb<=cb+chunk;chunk<=ci-(cb+chunk)>32 ? 32:ci-(cb+chunk);zero_word<=0;state<=GEOM;end
 ZERO_BIAS:if((zero_word+1)*16==bias_bytes)begin zero_word<=0;state<=input_cached ? I_PREP:SCAN;end else zero_word<=zero_word+1;
 SCAN:begin
 zero_input_word<=0;read_pixels<=1;
 if(inside_image)begin
 if(contiguous_channels)
 read_pixels<=window_remaining<image_remaining ? window_remaining[5:0]:image_remaining[5:0];
 pixel_row<=gy[7:0]*fw[8:0];state<=ADDR_PIXEL;end
 else state<=ZERO_INPUT;
 end
 ADDR_PIXEL:begin pixel_index<=pixel_row+gx[7:0];state<=ADDR_SCALE;end
 ADDR_SCALE:begin pixel_offset<=pixel_index*full_stride[9:0];state<=ADDR_BASE;end
 ADDR_BASE:begin pixel_base<=ab+pixel_offset;state<=INPUT;end
 ZERO_INPUT:if((zero_input_word+1)*16==local_stride)state<=SCAN_ADV;else zero_input_word<=zero_input_word+1;
 SCAN_ADV:begin
 input_offset<=input_offset+(two_words ? {read_pixels,1'b0}:{1'b0,read_pixels});
 if(ix+read_pixels==w)begin ix<=0;iy<=iy+1;if(iy+1==h)state<=W_PREP;else state<=SCAN;end
 else begin ix<=ix+read_pixels;state<=SCAN;end
 end
 I_PREP:if(input_hit||cb!=0)state<=COPY_I;
 else begin iy<=0;ix<=0;cache_offset<=0;state<=I_SCAN;end
 I_SCAN:begin
 cache_zero_word<=0;read_pixels<=1;
 if(inside_image)begin
 read_pixels<=window_remaining<image_remaining ? window_remaining[5:0]:image_remaining[5:0];
 pixel_row<=gy[7:0]*fw[8:0];state<=I_PIXEL;end else state<=I_ZERO;
 end
 I_PIXEL:begin pixel_index<=pixel_row+gx[7:0];state<=I_SCALE;end
 I_SCALE:begin pixel_offset<=pixel_index*full_stride[9:0];state<=I_BASE;end
 I_BASE:begin pixel_base<=ab+pixel_offset;input_row_bytes<=read_pixels*full_stride;state<=I_READ;end
 I_READ:if(dr&&dma_arm)state<=I_WAIT;
 I_WAIT:if(dd)begin
 if(df)begin failed<=1;error_code<=de;state<=FINISH;end
 else begin read_bytes<=read_bytes+db;cache_offset<=cache_offset+(db>>4);state<=I_ADV;end end
 I_ZERO:if(cache_zero_word+1==channel_words)begin cache_offset<=cache_offset+channel_words;state<=I_ADV;end
 else cache_zero_word<=cache_zero_word+1;
 I_ADV:if(ix+read_pixels==w)begin ix<=0;iy<=iy+1;if(iy+1==h)state<=COPY_I;else state<=I_SCAN;end
 else begin ix<=ix+read_pixels;state<=I_SCAN;end
 W_PREP:if(!weight_cached)state<=WEIGHTS;
 else if(weight_hit||cb!=0)state<=COPY_W;else state<=W_READ;
 W_READ:if(dr&&dma_arm)state<=W_WAIT;
 W_WAIT:if(dd)begin
 if(df)begin failed<=1;error_code<=de;state<=FINISH;end
 else begin read_bytes<=read_bytes+db;state<=COPY_W;end end
 COPY_I:begin copy_weights<=0;copy_source<=cb>>4;copy_dest<=0;copy_second<=0;copy_pixel<=0;copy_count<=pixels;state<=COPY_STREAM;end
 COPY_W:begin copy_weights<=1;copy_source<=cb>>4;copy_dest<=0;copy_second<=0;copy_pixel<=0;copy_count<=weight_rows;state<=COPY_STREAM;end
 COPY_STREAM:begin
 // Current cache read enters input_q/weight_cache_q while the preceding word writes.
 copy_dest<=copy_dest+1;
 if(two_words&&!copy_second)begin copy_second<=1;copy_source<=copy_source+1;end
 else if(copy_pixel+1==copy_count)state<=COPY_DRAIN;
 else begin copy_pixel<=copy_pixel+1;copy_second<=0;copy_source<=copy_source+copy_pixel_step;end
 end
 COPY_DRAIN:state<=copy_weights ? LAUNCH:W_PREP;
 FINISH:begin done<=1;state<=IDLE;end
 default:begin failed<=1;error_code<=8;state<=FINISH;end
 endcase end end
endmodule
