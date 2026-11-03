`timescale 1ns/1ps
// One depthwise C16 group; bounded full-width row strips are shared across X/groups.
// Whole-layer DW weights/bias are cached only under explicit layer lifetime control.
// Virtual off-image pixels are literal signed zero; no out-of-range DDR read.
module dw_window #(parameter WATCHDOG=100000)(
 input clk,reset,cmd_valid,output cmd_ready,
 input cfg_reuse_input,cfg_reuse_parameters,
 input [29:0] cfg_full_weight,cfg_full_bias,
 input [15:0] cfg_h,cfg_w,cfg_ci,cfg_co,input [1:0] cfg_k,cfg_s,cfg_p,
 input [15:0] cfg_full_h,cfg_full_w,cfg_channel_base,
 input signed [16:0] cfg_origin_y,cfg_origin_x,
 input [29:0] cfg_activation,cfg_weight,cfg_bias,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready,
 output out_valid,input out_ready,output [511:0] out_result,output [15:0] out_mask,
 output [15:0] out_y,out_x,out_channel,
 output reg done,failed,output reg [7:0] error_code,
 output reg [31:0] batches,read_bytes,output reg [31:0] partial_vectors,final_vectors
);
 localparam IDLE=0,GEOM=1,SIZES=2,CHECK=3,BIAS=4,BIAS_WAIT=5,INPUT=6,INPUT_WAIT=7,WEIGHTS=8,WEIGHTS_WAIT=9,LAUNCH=10,RUN=11,NEXT=12,ZERO_BIAS=13,FINISH=14,SCAN=15,ZERO_INPUT=16,SCAN_ADV=17,ADDR_PIXEL=18,ADDR_SCALE=19,ADDR_BASE=20;
 localparam P_PREP=21,PW_READ=22,PW_WAIT=23,PB_READ=24,PB_WAIT=25,B_INIT=26,B_READ=27,B_STORE=28,
 I_PREP=29,I_INIT=30,I_ROW=31,I_SCALE=33,I_BASE=34,I_READ=35,I_WAIT=36,I_NEXT=37,
 C_INIT=38,C_ROW=39,C_PIXEL=40,C_SCALE=41,C_READ=42,C_STORE=43,C_ZERO=44,
 W_INIT=45,W_READ=46,W_STORE=47,SIZES2=48;
 reg [5:0] state;
 reg [15:0] fh,fw,iy,ix,pixel_row,pixel_index;reg [16:0] source_pixels;reg [25:0] pixel_offset;reg signed [16:0] origin_y,origin_x;
 reg [11:0] input_offset;reg [1:0] zero_input_word;
 wire signed [17:0] gy=$signed(origin_y)+$signed({1'b0,iy});
 wire signed [17:0] gx=$signed(origin_x)+$signed({1'b0,ix});
 wire inside_image=gy>=0&&gx>=0&&gy<$signed({1'b0,fh})&&gx<$signed({1'b0,fw});
 reg [29:0] pixel_base;reg [15:0] h,w,ci,co,oh,ow,cb,chunk;
 reg [1:0] k,s,p;reg [29:0] ab,wb,bb;
 reg [31:0] pixels,out_pixels,input_bytes,weight_bytes,source_bytes,source_weight_bytes,psum_words;
 reg [15:0] full_stride,local_stride,weight_rows,bias_bytes;reg [3:0] zero_word;
 reg [29:0] full_wb,full_bb,expected_wb,expected_bb;
 reg [31:0] source_row_bytes,stripe_bytes,all_weight_bytes,all_bias_bytes,group_weight_offset;
 reg input_cached,input_valid,parameters_valid,input_hit,parameters_hit;
 reg [13:0] cache_offset;reg [15:0] row_words;reg [5:0] channel_words;
 reg [12:0] copy_address;reg [9:0] weight_index;reg [3:0] copy_index;
 reg [127:0] input_q,weight_q,bias_q;
 reg [29:0] tag_ab,tag_wb,tag_bb;reg [15:0] tag_h,tag_fh,tag_fw,tag_ci,tag_pci;
 reg signed [16:0] tag_oy;reg [1:0] tag_k;
 (* ram_style="block" *) reg [127:0] input_cache[0:8191];
 (* ram_style="block" *) reg [127:0] weight_cache[0:1023];
 (* ram_style="block" *) reg [127:0] bias_cache[0:255];
 assign cmd_ready=state==IDLE&&!failed;
 wire dr,dd,df,dwe;wire [7:0] de;wire [31:0] db;wire [12:0] da;wire [127:0] ddata;
 wire issuing=state==INPUT||state==PW_READ||state==PB_READ||state==I_READ;
 wire [29:0] base=state==PW_READ ? full_wb:state==PB_READ ? full_bb:state==I_READ ? pixel_base:pixel_base+cb;
 wire [29:0] stride=30'd0;
 wire [15:0] row_bytes=state==PW_READ ? all_weight_bytes[15:0]:state==PB_READ ? all_bias_bytes[15:0]:state==I_READ ? source_row_bytes[15:0]:16'd16;
 wire [15:0] rows=16'd1;
 axi_tile_dma_guarded #(.WORD_ADDR_BITS(13),.WATCHDOG(WATCHDOG)) dma(.clk(clk),.reset(reset),.cmd_valid(issuing),.cmd_ready(dr),.cmd_write(1'b0),
 .cmd_base(base),.cmd_stride(stride),.cmd_row_bytes(row_bytes),.cmd_rows(rows),.done(dd),.failed(df),.error_code(de),.transferred_bytes(db),
 .awready(1'b0),.wready(1'b0),.bresp(2'd0),.bvalid(1'b0),
 .araddr(araddr),.arlen(arlen),.arvalid(arvalid),.arready(arready),.rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid),.rready(rready),
 .mem_we(dwe),.mem_addr(da),.mem_wdata(ddata),.mem_rdata(128'd0));
 wire ce,cr,cd,cf,proto,cv;wire [11:0] ca;reg [127:0] cq;
 wire [511:0] result;wire [15:0] overflow,cx,cy,cc;
 wire ps_ready=out_ready,ps_busy=1'b0,ps_fail=1'b0;reg conv_finished;reg [9:0] ordinal;
 wire [7:0] mask=8'hff>>(co-cc>=8 ? 0:8-(co-cc));
 conv_pipeline #(.DEPTHWISE(1)) conv(.clk(clk),.reset(reset),.cmd_valid(state==LAUNCH),.cmd_ready(cr),
 .cfg_h(h),.cfg_w(w),.cfg_ci(chunk),.cfg_co(co),.cfg_oh(oh),.cfg_ow(ow),.cfg_kernel(k),.cfg_stride(s),.cfg_pad(p),
 .cfg_activation(16'd0),.cfg_weight(16'hc000),.cfg_bias(16'hf800),.cfg_gaps(1'b0),
 .mem_addr(ca),.mem_en(ce),.mem_q(cq),.out_valid(cv),.out_ready(ps_ready&&state==RUN),.out_result(result),.out_overflow(overflow),
 .out_x(cx),.out_y(cy),.out_channel(cc),.done(cd),.failed(cf),.protocol_error(proto));
 assign out_valid=cv&&state==RUN;
 genvar lane;
 generate for(lane=0;lane<16;lane=lane+1)begin: mask_result
 assign out_result[lane*32+:32]=lane<co ? result[lane*32+:32]:32'd0;
 end endgenerate
 assign out_mask=16'hffff>>(16-co);
 assign out_channel=cc;assign out_y=cy;assign out_x=cx;
 always @(posedge clk)begin
 if(reset)begin partial_vectors<=0;final_vectors<=0;end
 else if(out_valid&&out_ready)begin partial_vectors<=partial_vectors+1;final_vectors<=final_vectors+1;end
 end
 (* ram_style="block" *) reg [127:0] local_ram[0:4095];
 wire [11:0] write_address=state==ZERO_BIAS ? 12'hf80+zero_word:state==BIAS_WAIT ? 12'hf80+da:state==WEIGHTS_WAIT ? 12'hc00+da:state==ZERO_INPUT ? input_offset+zero_input_word:input_offset+da;
 wire local_zero=state==ZERO_INPUT||state==C_ZERO;
 wire local_we=(dwe&&state==INPUT_WAIT)||local_zero||state==B_STORE||state==W_STORE||state==C_STORE;
 wire [11:0] local_wa=state==B_STORE ? 12'hf80+copy_index:state==W_STORE ? 12'hc00+copy_index:
 state==C_STORE||state==C_ZERO ? input_offset:write_address;
 wire [127:0] local_wd=local_zero ? 128'd0:state==B_STORE ? bias_q:state==W_STORE ? weight_q:state==C_STORE ? input_q:ddata;
 wire [12:0] cache_wa=cache_offset+da;
 always @(posedge clk)begin
 if(ce)cq<=local_ram[ca];
 if(local_we)local_ram[local_wa]<=local_wd;
 if(dwe&&state==I_WAIT)input_cache[cache_wa]<=ddata;
 if(dwe&&state==PW_WAIT)weight_cache[da[9:0]]<=ddata;
 if(dwe&&state==PB_WAIT)bias_cache[da[7:0]]<=ddata;
 if(state==C_READ)input_q<=input_cache[copy_address];
 if(state==W_READ)weight_q<=weight_cache[weight_index+copy_index];
 if(state==B_READ)bias_q<=bias_cache[(cb>>2)+copy_index];
 end
 wire [31:0] full_cp=((ci+15)>>4)<<4;
 always @(posedge clk)begin
 if(reset)begin
 full_wb<=0;full_bb<=0;expected_wb<=0;expected_bb<=0;source_row_bytes<=0;stripe_bytes<=0;
 all_weight_bytes<=0;all_bias_bytes<=0;group_weight_offset<=0;input_cached<=0;input_valid<=0;parameters_valid<=0;input_hit<=0;parameters_hit<=0;
 cache_offset<=0;row_words<=0;channel_words<=0;copy_address<=0;weight_index<=0;copy_index<=0;
 tag_ab<=0;tag_wb<=0;tag_bb<=0;tag_h<=0;tag_fh<=0;tag_fw<=0;tag_ci<=0;tag_pci<=0;tag_oy<=0;tag_k<=0;
 end else begin
 if(state==IDLE&&cmd_valid&&cmd_ready)begin
 full_wb<=cfg_full_weight;full_bb<=cfg_full_bias;
 input_hit<=cfg_reuse_input&&input_valid&&tag_ab==cfg_activation&&tag_h==cfg_h&&tag_fh==cfg_full_h&&tag_fw==cfg_full_w&&tag_ci==cfg_ci&&tag_oy==cfg_origin_y;
 parameters_hit<=cfg_reuse_parameters&&parameters_valid&&tag_wb==cfg_full_weight&&tag_bb==cfg_full_bias&&tag_pci==cfg_ci&&tag_k==cfg_k;
 input_valid<=0;parameters_valid<=0;
 tag_ab<=cfg_activation;tag_h<=cfg_h;tag_fh<=cfg_full_h;tag_fw<=cfg_full_w;tag_ci<=cfg_ci;tag_oy<=cfg_origin_y;
 tag_wb<=cfg_full_weight;tag_bb<=cfg_full_bias;tag_pci<=cfg_ci;tag_k<=cfg_k;
 end
 if(state==SIZES)begin source_row_bytes<=fw*full_stride;all_weight_bytes<=weight_rows*full_stride;all_bias_bytes<=full_stride*4;
 group_weight_offset<=cb*(k==3 ? 9:1);end
 if(state==SIZES2)begin stripe_bytes<=h*source_row_bytes;expected_wb<=full_wb+group_weight_offset;expected_bb<=full_bb+cb*4;end
 if(state==CHECK)begin input_cached<=source_row_bytes!=0&&source_row_bytes<65536&&stripe_bytes<=131072;
 row_words<=source_row_bytes>>4;channel_words<=full_stride>>4;end
 if(state==P_PREP&&parameters_hit)parameters_valid<=1;
 if(state==PB_WAIT&&dd&&!df)parameters_valid<=1;
 if(state==I_PREP&&input_cached&&input_hit)input_valid<=1;
 if(state==I_NEXT&&iy+1==h)input_valid<=1;
 if(state==FINISH&&failed)begin input_valid<=0;parameters_valid<=0;end
 end
 if(reset)begin state<=IDLE;fh<=0;fw<=0;origin_y<=0;origin_x<=0;iy<=0;ix<=0;input_offset<=0;zero_input_word<=0;pixel_base<=0;pixel_row<=0;pixel_index<=0;pixel_offset<=0;source_pixels<=0;h<=0;w<=0;ci<=0;co<=0;oh<=0;ow<=0;cb<=0;chunk<=0;k<=0;s<=0;p<=0;ab<=0;wb<=0;bb<=0;pixels<=0;out_pixels<=0;input_bytes<=0;weight_bytes<=0;source_bytes<=0;source_weight_bytes<=0;psum_words<=0;full_stride<=0;local_stride<=0;weight_rows<=0;bias_bytes<=0;zero_word<=0;done<=0;failed<=0;error_code<=0;batches<=0;read_bytes<=0;ordinal<=0;conv_finished<=0;end
 else begin done<=0;case(state)
 IDLE:if(cmd_valid&&cmd_ready)begin h<=cfg_h;w<=cfg_w;fh<=cfg_full_h;fw<=cfg_full_w;origin_y<=cfg_origin_y;origin_x<=cfg_origin_x;ci<=cfg_ci;co<=cfg_co;k<=cfg_k;s<=cfg_s;p<=cfg_p;ab<=cfg_activation;wb<=cfg_weight;bb<=cfg_bias;cb<=cfg_channel_base;chunk<=cfg_co;batches<=0;read_bytes<=0;error_code<=0;state<=GEOM;end
 GEOM:begin
 iy<=0;ix<=0;input_offset<=0;zero_input_word<=0;pixels<=h*w;source_pixels<=fh[8:0]*fw[8:0];oh<=((h+2*p-k)>> (s==2 ? 1:0))+1;ow<=((w+2*p-k)>>(s==2 ? 1:0))+1;
 full_stride<=full_cp;local_stride<=16;weight_rows<=k==3 ? 9:1;bias_bytes<=64;state<=SIZES;
 end
 SIZES:begin input_bytes<=pixels*local_stride;weight_bytes<=weight_rows*local_stride;out_pixels<=oh*ow;source_bytes<=source_pixels*full_stride[9:0];source_weight_bytes<=weight_rows*16;psum_words<=oh*ow;state<=SIZES2;end
 SIZES2:state<=CHECK;
 CHECK:if(fh==0||fw==0||fh>256||fw>256||p!=0||h==0||w==0||h>32||w>32||ci==0||ci>960||co==0||co>16||cb[3:0]!=0||cb+co>ci||(k!=1&&k!=3)||(s!=1&&s!=2)||p>1||h+2*p<k||w+2*p<k||input_bytes>32768||weight_bytes>14336||out_pixels>1024||psum_words>1024||ab[3:0]!=0||wb[3:0]!=0||bb[3:0]!=0||full_wb[3:0]!=0||full_bb[3:0]!=0||wb!=expected_wb||bb!=expected_bb||{1'b0,ab}+source_bytes>31'h40000000||{1'b0,full_wb}+all_weight_bytes>31'h40000000||{1'b0,full_bb}+all_bias_bytes>31'h40000000)begin failed<=1;error_code<=6;state<=FINISH;end else state<=P_PREP;
 BIAS:if(dr)state<=BIAS_WAIT;
 BIAS_WAIT:if(dd)begin if(df)begin failed<=1;error_code<=de;state<=FINISH;end else begin read_bytes<=read_bytes+db;state<=SCAN;end end
 INPUT:if(dr)state<=INPUT_WAIT;
 INPUT_WAIT:if(dd)begin if(df)begin failed<=1;error_code<=de;state<=FINISH;end else begin read_bytes<=read_bytes+db;state<=SCAN_ADV;end end
 WEIGHTS:if(dr)state<=WEIGHTS_WAIT;
 WEIGHTS_WAIT:if(dd)begin if(df)begin failed<=1;error_code<=de;state<=FINISH;end else begin read_bytes<=read_bytes+db;state<=LAUNCH;end end
 LAUNCH:if(cr)begin ordinal<=0;conv_finished<=0;state<=RUN;end
 RUN:begin
 if(cv&&ps_ready)begin ordinal<=ordinal+1;if(|(overflow&out_mask))begin failed<=1;error_code<=7;state<=FINISH;end end
 if(cd)conv_finished<=1;
 if(cf||proto||ps_fail)begin failed<=1;error_code<=7;state<=FINISH;end
 else if(conv_finished&&!ps_busy)begin batches<=batches+1;state<=NEXT;end
 end
 NEXT:state<=FINISH;
 ZERO_BIAS:if((zero_word+1)*16==bias_bytes)begin zero_word<=0;state<=SCAN;end else zero_word<=zero_word+1;
 SCAN:begin
 zero_input_word<=0;
 if(inside_image)begin pixel_row<=gy[7:0]*fw[8:0];state<=ADDR_PIXEL;end
 else state<=ZERO_INPUT;
 end
 ADDR_PIXEL:begin pixel_index<=pixel_row+gx[7:0];state<=ADDR_SCALE;end
 ADDR_SCALE:begin pixel_offset<=pixel_index*full_stride[9:0];state<=ADDR_BASE;end
 ADDR_BASE:begin pixel_base<=ab+pixel_offset;state<=INPUT;end
 ZERO_INPUT:if((zero_input_word+1)*16==local_stride)state<=SCAN_ADV;else zero_input_word<=zero_input_word+1;
 SCAN_ADV:begin
 input_offset<=input_offset+(local_stride>>4);
 if(ix+1==w)begin ix<=0;iy<=iy+1;if(iy+1==h)state<=W_INIT;else state<=SCAN;end
 else begin ix<=ix+1;state<=SCAN;end
 end
 P_PREP:state<=parameters_hit ? B_INIT:PW_READ;
 PW_READ:if(dr)state<=PW_WAIT;
 PW_WAIT:if(dd)begin if(df)begin failed<=1;error_code<=de;state<=FINISH;end else begin read_bytes<=read_bytes+db;state<=PB_READ;end end
 PB_READ:if(dr)state<=PB_WAIT;
 PB_WAIT:if(dd)begin if(df)begin failed<=1;error_code<=de;state<=FINISH;end else begin read_bytes<=read_bytes+db;state<=B_INIT;end end
 B_INIT:begin copy_index<=0;state<=B_READ;end
 B_READ:state<=B_STORE;
 B_STORE:if(copy_index==3)state<=I_PREP;else begin copy_index<=copy_index+1;state<=B_READ;end
 I_PREP:if(!input_cached)state<=SCAN;else if(input_hit)state<=C_INIT;else state<=I_INIT;
 I_INIT:begin iy<=0;ix<=0;cache_offset<=0;state<=I_ROW;end
 I_ROW:if(gy<0||gy>=$signed({1'b0,fh}))state<=I_NEXT;
 else begin pixel_row<=gy[7:0]*fw[8:0];state<=I_SCALE;end
 I_SCALE:begin pixel_offset<=pixel_row*full_stride[9:0];state<=I_BASE;end
 I_BASE:begin pixel_base<=ab+pixel_offset;state<=I_READ;end
 I_READ:if(dr)state<=I_WAIT;
 I_WAIT:if(dd)begin if(df)begin failed<=1;error_code<=de;state<=FINISH;end else begin read_bytes<=read_bytes+db;state<=I_NEXT;end end
 I_NEXT:begin cache_offset<=cache_offset+row_words;iy<=iy+1;if(iy+1==h)state<=C_INIT;else state<=I_ROW;end
 C_INIT:begin iy<=0;ix<=0;input_offset<=0;state<=C_ROW;end
 C_ROW:if(!inside_image)state<=C_ZERO;else begin pixel_row<=iy[5:0]*fw[8:0];state<=C_PIXEL;end
 C_PIXEL:begin pixel_index<=pixel_row+gx[7:0];state<=C_SCALE;end
 C_SCALE:begin copy_address<=pixel_index*channel_words+(cb>>4);state<=C_READ;end
 C_READ:state<=C_STORE;
 C_STORE,C_ZERO:begin
 input_offset<=input_offset+1;
 if(ix+1==w)begin ix<=0;iy<=iy+1;if(iy+1==h)state<=W_INIT;else state<=C_ROW;end
 else begin ix<=ix+1;state<=C_ROW;end
 end
 W_INIT:begin copy_index<=0;weight_index<=(cb>>4)*weight_rows;state<=W_READ;end
 W_READ:state<=W_STORE;
 W_STORE:if(copy_index+1==weight_rows)state<=LAUNCH;else begin copy_index<=copy_index+1;state<=W_READ;end
 FINISH:begin done<=1;state<=IDLE;end
 default:begin failed<=1;error_code<=8;state<=FINISH;end
 endcase end end
endmodule
