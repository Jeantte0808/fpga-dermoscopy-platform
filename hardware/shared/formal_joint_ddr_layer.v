`timescale 1ns/1ps
// Full-channel tile output assembly -> contiguous row bursts. Arithmetic/layout unchanged.
// Formal per-channel parameters; reload only when idle and fully drained.
module formal_joint_ddr_layer #(parameter WATCHDOG=100000)(
 input clk,reset,cmd_valid,output cmd_ready,
 input param_clear,param_valid,output param_ready,input [31:0] param_multiplier,
 input [7:0] param_shift,param_min,param_max,input param_commit,
 input [15:0] param_channels,output param_configured,param_error,
 input [15:0] cfg_h,cfg_w,cfg_ci,cfg_co,input [1:0] cfg_k,cfg_s,cfg_p,
 input [29:0] cfg_activation,cfg_weight,cfg_bias,cfg_output,
 output [29:0] awaddr,output [7:0] awlen,output awvalid,input awready,
 output [127:0] wdata,output wlast,wvalid,input wready,input [1:0] bresp,input bvalid,output bready,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready,
 output reg done,failed,output reg [7:0] error_code,
 output [31:0] tile_groups,channel_batches,read_bytes,output reg [31:0] write_bytes,
 output [31:0] partial_vectors,final_vectors,output reg [31:0] valid_scalars
);
 localparam IDLE=0,CHECK=1,START=2,RUN=3,DRAIN=4,STORE=5,STORE_WAIT=6,COMMIT=7,FINISH=8,GEOM=9,SIZES=10,SIZES2=11,ROW_ADDR=12,PIXEL_ADDR=13,SCALE_ADDR=14,BASE_ADDR=15;
 reg [3:0] state;reg [15:0] h,w,ci,co,out_cp,group_cp,store_row,store_bytes;
 reg [5:0] local_row_pixel;reg [10:0] local_row_base;
 reg [1:0] k,s,p;reg [29:0] ab,wb,bb,ob;reg [31:0] pending;
 reg [9:0] input_cp;reg [16:0] input_pixels,output_pixels,row_pixel,pixel_index;reg [13:0] weight_rows;
 reg [15:0] expected_oh,expected_ow;reg [31:0] input_size,weight_size,bias_size,output_size;reg [26:0] byte_offset;reg [29:0] output_row;
 wire jr,jd,jf,gd;wire [7:0] je;wire [15:0] ty,tx,th,tw,cg,gco,oh,ow;
 wire ov,qr,qv;wire [255:0] values;wire [7:0] mask;wire [15:0] oy,ox,oc;
 assign cmd_ready=state==IDLE&&!failed&&param_configured&&!param_error&&!param_clear&&!param_valid&&!param_commit&&param_channels==cfg_co;
 joint_layer #(.WATCHDOG(WATCHDOG),.EXTERNAL_COMMIT(1)) layer(
 .clk(clk),.reset(reset),.cmd_valid(state==START),.cmd_ready(jr),
 .cfg_h(h),.cfg_w(w),.cfg_ci(ci),.cfg_co(co),.cfg_k(k),.cfg_s(s),.cfg_p(p),.cfg_activation(ab),.cfg_weight(wb),.cfg_bias(bb),
 .araddr(araddr),.arlen(arlen),.arvalid(arvalid),.arready(arready),.rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid),.rready(rready),
 .out_valid(ov),.out_ready(qr&&state==RUN),.out_result(values),.out_mask(mask),.out_y(oy),.out_x(ox),.out_channel(oc),
 .done(jd),.failed(jf),.error_code(je),.tile_groups(tile_groups),.channel_batches(channel_batches),.read_bytes(read_bytes),.partial_vectors(partial_vectors),.final_vectors(final_vectors),
 .group_done(gd),.group_commit(state==COMMIT),.tile_y(ty),.tile_x(tx),.tile_h(th),.tile_w(tw),.group_channel(cg),.group_channels(gco),.output_h(oh),.output_w(ow));
 wire [127:0] qdata;wire [15:0] qmask,qe;wire [63:0] tag;
 wire param_bank_ready;
 assign param_ready=param_bank_ready&&state==IDLE&&!failed&&!param_clear;
 formal_quant_banked quant(.clk(clk),.reset(reset||(param_clear&&state==IDLE&&!failed&&pending==0)),
 .cfg_valid(param_valid&&param_ready),.cfg_ready(param_bank_ready),.cfg_multiplier(param_multiplier),.cfg_shift(param_shift),
 .cfg_min(param_min),.cfg_max(param_max),.cfg_commit(param_commit&&state==IDLE&&!failed&&!param_clear),.cfg_channels(param_channels),
 .configured(param_configured),.cfg_error(param_error),.in_valid(ov&&state==RUN),.in_ready(qr),.in_values({256'd0,values}),
 .in_mask({8'd0,mask}),.in_channel(oc),.in_tag({16'd0,oc,oy,ox}),
 .out_valid(qv),.out_ready(1'b1),.out_values(qdata),.pre_values(),.out_mask(qmask),.out_error(qe),.out_tag(tag));
 wire accepted=ov&&qr&&state==RUN;
 reg [4:0] lane_count;integer lane;
 always @* begin lane_count=0;for(integer l=0;l<16;l=l+1)lane_count=lane_count+qmask[l];end
 // One complete 4x5 tile, up to960 logical channels in unchanged HWC C16.
 // Distinct channel groups assemble before the first DDR write; no stale RAM fill.
 (* ram_style="block" *) reg [127:0] result_ram[0:2047];
 wire [15:0] ly=tag[31:16]-ty,lx=tag[15:0]-tx;
 reg capture_valid,write_valid;reg [5:0] capture_pixel,capture_channel;
 reg [10:0] write_address;reg capture_high,write_high;
 reg [127:0] capture_data,write_data;reg [15:0] capture_mask,write_mask;
 wire store_ready,store_done,store_fail,unused_we;wire [7:0] store_error;wire [31:0] transferred;
 wire [10:0] ma;wire [127:0] unused_data;reg [127:0] mq;
 wire [10:0] ra=local_row_base+ma;
 integer j;
 always @(posedge clk)begin
 if(reset)begin capture_valid<=0;write_valid<=0;capture_pixel<=0;capture_channel<=0;capture_high<=0;write_high<=0;
 capture_data<=0;write_data<=0;capture_mask<=0;write_mask<=0;write_address<=0;end
 else begin
 capture_valid<=qv;write_valid<=capture_valid;
 if(qv)begin capture_pixel<=ly[1:0]*tw[2:0]+lx[2:0];capture_channel<=tag[41:36];capture_high<=tag[35];capture_data<=qdata;capture_mask<=qmask;end
 if(capture_valid)begin write_address<=capture_pixel*out_cp[9:4]+capture_channel;write_high<=capture_high;write_data<=capture_data;write_mask<=capture_mask;end
 end end
 always @(posedge clk)begin
 mq<=result_ram[ra];
 if(write_valid)for(j=0;j<16;j=j+1)begin
 if(!write_high)begin
 if(j<8)result_ram[write_address][j*8+:8]<=write_mask[j] ? write_data[j*8+:8]:8'd0;
 else result_ram[write_address][j*8+:8]<=0;
 end else if(j>=8)result_ram[write_address][j*8+:8]<=write_mask[j-8] ? write_data[(j-8)*8+:8]:8'd0;
 end
 end
 axi_tile_dma_guarded #(.WORD_ADDR_BITS(11),.WATCHDOG(WATCHDOG)) writer(
 .clk(clk),.reset(reset),.cmd_valid(state==STORE),.cmd_ready(store_ready),.cmd_write(1'b1),.cmd_base(output_row),.cmd_stride(30'd0),.cmd_row_bytes(store_bytes),.cmd_rows(16'd1),
 .done(store_done),.failed(store_fail),.error_code(store_error),.transferred_bytes(transferred),
 .awaddr(awaddr),.awlen(awlen),.awvalid(awvalid),.awready(awready),.wdata(wdata),.wlast(wlast),.wvalid(wvalid),.wready(wready),.bresp(bresp),.bvalid(bvalid),.bready(bready),
 .arready(1'b0),.rdata(128'd0),.rresp(2'd0),.rlast(1'b0),.rvalid(1'b0),.mem_we(unused_we),.mem_addr(ma),.mem_wdata(unused_data),.mem_rdata(mq));
 // Source/destination overlap is forbidden for this layer (no in-place overwrite).
 wire overlap_a=ob<({1'b0,ab}+input_size)&&ab<({1'b0,ob}+output_size);
 wire overlap_w=ob<({1'b0,wb}+weight_size)&&wb<({1'b0,ob}+output_size);
 wire overlap_b=ob<({1'b0,bb}+bias_size)&&bb<({1'b0,ob}+output_size);
 always @(posedge clk)begin
 if(reset)begin state<=IDLE;valid_scalars<=0;input_cp<=0;input_pixels<=0;output_pixels<=0;weight_rows<=0;expected_oh<=0;expected_ow<=0;input_size<=0;weight_size<=0;bias_size<=0;output_size<=0;row_pixel<=0;pixel_index<=0;byte_offset<=0;output_row<=0;h<=0;w<=0;ci<=0;co<=0;k<=0;s<=0;p<=0;ab<=0;wb<=0;bb<=0;ob<=0;out_cp<=0;group_cp<=0;store_row<=0;store_bytes<=0;local_row_pixel<=0;local_row_base<=0;pending<=0;write_bytes<=0;done<=0;failed<=0;error_code<=0;end
 else begin done<=0;
 if(qv)valid_scalars<=valid_scalars+lane_count;
 case({accepted,qv})2'b10:pending<=pending+1;2'b01:pending<=pending-1;default:begin end endcase
 case(state)
 IDLE:if(cmd_valid&&cmd_ready)begin h<=cfg_h;w<=cfg_w;ci<=cfg_ci;co<=cfg_co;k<=cfg_k;s<=cfg_s;p<=cfg_p;ab<=cfg_activation;wb<=cfg_weight;bb<=cfg_bias;ob<=cfg_output;out_cp<=((cfg_co+15)>>4)<<4;pending<=0;write_bytes<=0;valid_scalars<=0;state<=GEOM;end
 GEOM:begin
 expected_oh<=((h+2*p-k)>>(s==2 ? 1:0))+1;expected_ow<=((w+2*p-k)>>(s==2 ? 1:0))+1;
 input_cp<=((ci+15)>>4)<<4;input_pixels<=h[8:0]*w[8:0];weight_rows<=co[9:0]*(k==3 ? 4'd9:4'd1);bias_size<=((co+7)>>3)*32;state<=SIZES;end
 SIZES:begin input_size<=input_pixels*input_cp;weight_size<=weight_rows*input_cp;output_pixels<=expected_oh[8:0]*expected_ow[8:0];state<=SIZES2;end
 SIZES2:begin output_size<=output_pixels*out_cp[9:0];state<=CHECK;end
 CHECK:if(h==0||w==0||h>256||w>256||ci==0||ci>960||co==0||co>960||(k!=1&&k!=3)||(s!=1&&s!=2)||p>1||h+2*p<k||w+2*p<k||ob[3:0]!=0||{1'b0,ob}+output_size>31'h40000000||overlap_a||overlap_w||overlap_b)
 begin failed<=1;error_code<=6;state<=FINISH;end else state<=START;
 START:if(jr)state<=RUN;
 RUN:begin if(jd)begin if(jf)begin failed<=1;error_code<=je;end state<=FINISH;end
 else if(gd)state<=DRAIN;end
 DRAIN:if(pending==0&&!qv&&!capture_valid&&!write_valid)begin
 if(cg+gco<co)state<=COMMIT;
 else begin group_cp<=out_cp;store_bytes<=tw*out_cp;store_row<=0;state<=ROW_ADDR;end end
 ROW_ADDR:begin local_row_pixel<=store_row[1:0]*tw[2:0];row_pixel<=(ty[8:0]+store_row[1:0])*ow[8:0];state<=PIXEL_ADDR;end
 PIXEL_ADDR:begin local_row_base<=local_row_pixel*out_cp[9:4];pixel_index<=row_pixel+tx[8:0];state<=SCALE_ADDR;end
 SCALE_ADDR:begin byte_offset<=pixel_index*out_cp[9:0];state<=BASE_ADDR;end
 BASE_ADDR:begin output_row<=ob+byte_offset;state<=STORE;end
 STORE:if(store_ready)state<=STORE_WAIT;
 STORE_WAIT:if(store_done)begin
 if(store_fail)begin failed<=1;error_code<=store_error;state<=FINISH;end
 else begin write_bytes<=write_bytes+transferred;if(store_row+1==th)state<=COMMIT;else begin store_row<=store_row+1;state<=ROW_ADDR;end end end
 COMMIT:state<=RUN;
 FINISH:begin done<=1;state<=IDLE;end
 default:begin failed<=1;error_code<=8;state<=FINISH;end
 endcase
 if(qv&&|qe)begin failed<=1;error_code<=7;state<=FINISH;end
 end end
endmodule
