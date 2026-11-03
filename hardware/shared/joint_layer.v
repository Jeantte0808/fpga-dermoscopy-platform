`timescale 1ns/1ps
// Dense layer scheduler: spatial tiles, output groups, input-channel passes.
// Input/weights remain full tensors in DDR. Outputs are global tagged INT32.
module joint_layer #(parameter TILE_H=4,TILE_W=5,WATCHDOG=100000,EXTERNAL_COMMIT=0)(
 input clk,reset,cmd_valid,output cmd_ready,
 input [15:0] cfg_h,cfg_w,cfg_ci,cfg_co,input [1:0] cfg_k,cfg_s,cfg_p,
 input [29:0] cfg_activation,cfg_weight,cfg_bias,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready,
 output out_valid,input out_ready,output [255:0] out_result,output [7:0] out_mask,
 output [15:0] out_y,out_x,out_channel,
 output reg done,failed,output reg [7:0] error_code,
 output reg [31:0] tile_groups,channel_batches,read_bytes,
 output [31:0] partial_vectors,final_vectors,
 output group_done,input group_commit,
 output [15:0] tile_y,tile_x,tile_h,tile_w,group_channel,group_channels,output_h,output_w
);
 localparam IDLE=0,GEOM=1,CHECK=2,PREP=3,LAUNCH=4,RUN=5,ADVANCE=6,FINISH=7,SIZES=8,GROUP_SCALE=9,GROUP_BASE=10;
 reg [3:0] state;
 reg [15:0] h,w,ci,co,oh,ow,ty,tx,cg,th,tw,gco,vh,vw;
 reg [1:0] k,s,p;
 reg [29:0] ab,wb,bb,group_weight,group_bias;
 reg signed [16:0] origin_y,origin_x;
 reg [31:0] cp,input_size,weight_size,bias_size;
 reg [16:0] source_pixels;reg [13:0] weight_rows,group_rows;reg [23:0] group_offset;
 wire ir,id,iff;wire [7:0] ie;wire [15:0] ly,lx,lc;
 wire [31:0] ib,ird;
 assign cmd_ready=state==IDLE&&!failed;
 assign group_done=state==ADVANCE;
 assign tile_y=ty;assign tile_x=tx;assign tile_h=th;assign tile_w=tw;assign group_channel=cg;assign group_channels=gco;
 assign output_h=oh;assign output_w=ow;
 assign out_y=ty+ly;assign out_x=tx+lx;assign out_channel=cg+lc;
 channel_window #(.WATCHDOG(WATCHDOG)) window(
 .clk(clk),.reset(reset),.cmd_valid(state==LAUNCH),.cmd_ready(ir),
 .cfg_reuse_input(cg!=0),.cfg_reuse_weight(tx!=0||ty!=0),
 .cfg_h(vh),.cfg_w(vw),.cfg_ci(ci),.cfg_co(gco),.cfg_k(k),.cfg_s(s),.cfg_p(2'd0),
 .cfg_full_h(h),.cfg_full_w(w),.cfg_origin_y(origin_y),.cfg_origin_x(origin_x),
 .cfg_activation(ab),.cfg_weight(group_weight),.cfg_bias(group_bias),
 .araddr(araddr),.arlen(arlen),.arvalid(arvalid),.arready(arready),.rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid),.rready(rready),
 .out_valid(out_valid),.out_ready(out_ready),.out_result(out_result),.out_mask(out_mask),.out_y(ly),.out_x(lx),.out_channel(lc),
 .done(id),.failed(iff),.error_code(ie),.batches(ib),.read_bytes(ird),.partial_vectors(partial_vectors),.final_vectors(final_vectors));
 always @(posedge clk)begin
 if(reset)begin state<=IDLE;source_pixels<=0;weight_rows<=0;group_rows<=0;group_offset<=0;h<=0;w<=0;ci<=0;co<=0;k<=0;s<=0;p<=0;ab<=0;wb<=0;bb<=0;oh<=0;ow<=0;ty<=0;tx<=0;cg<=0;th<=0;tw<=0;gco<=0;vh<=0;vw<=0;group_weight<=0;group_bias<=0;origin_y<=0;origin_x<=0;cp<=0;input_size<=0;weight_size<=0;bias_size<=0;done<=0;failed<=0;error_code<=0;tile_groups<=0;channel_batches<=0;read_bytes<=0;end
 else begin done<=0;case(state)
 IDLE:if(cmd_valid&&cmd_ready)begin
 h<=cfg_h;w<=cfg_w;ci<=cfg_ci;co<=cfg_co;k<=cfg_k;s<=cfg_s;p<=cfg_p;ab<=cfg_activation;wb<=cfg_weight;bb<=cfg_bias;
 ty<=0;tx<=0;cg<=0;tile_groups<=0;channel_batches<=0;read_bytes<=0;error_code<=0;state<=GEOM;
 end
 GEOM:begin
 oh<=((h+2*p-k)>>(s==2 ? 1:0))+1;ow<=((w+2*p-k)>>(s==2 ? 1:0))+1;
 cp<=((ci+15)>>4)<<4;source_pixels<=h[8:0]*w[8:0];weight_rows<=co[9:0]*(k==3 ? 4'd9:4'd1);bias_size<=((co+7)>>3)*32;state<=SIZES;
 end
 SIZES:begin input_size<=source_pixels*cp[9:0];weight_size<=weight_rows*cp[9:0];state<=CHECK;end
 CHECK:if(h==0||w==0||h>256||w>256||ci==0||ci>960||co==0||co>960||(k!=1&&k!=3)||(s!=1&&s!=2)||p>1||h+2*p<k||w+2*p<k||
 TILE_H<1||TILE_W<1||TILE_H>4||TILE_W>5||ab[3:0]!=0||wb[3:0]!=0||bb[3:0]!=0||
 {1'b0,ab}+input_size>31'h40000000||{1'b0,wb}+weight_size>31'h40000000||{1'b0,bb}+bias_size>31'h40000000)
 begin failed<=1;error_code<=6;state<=FINISH;end else state<=PREP;
 PREP:begin
 th<=oh-ty>TILE_H ? TILE_H:oh-ty;tw<=ow-tx>TILE_W ? TILE_W:ow-tx;
 vh<=((oh-ty>TILE_H ? TILE_H:oh-ty)-1)*s+k;vw<=((ow-tx>TILE_W ? TILE_W:ow-tx)-1)*s+k;
 gco<=co-cg>32 ? 32:co-cg;origin_y<=$signed({1'b0,(s==2 ? {ty[14:0],1'b0}:ty)})-$signed({15'd0,p});origin_x<=$signed({1'b0,(s==2 ? {tx[14:0],1'b0}:tx)})-$signed({15'd0,p});
 group_rows<=cg[9:0]*(k==3 ? 4'd9:4'd1);group_bias<=bb+cg*4;state<=GROUP_SCALE;
 end
 GROUP_SCALE:begin group_offset<=group_rows*cp[9:0];state<=GROUP_BASE;end
 GROUP_BASE:begin group_weight<=wb+group_offset;state<=LAUNCH;end
 LAUNCH:if(ir)state<=RUN;
 RUN:if(id)begin
 if(iff)begin failed<=1;error_code<=ie;state<=FINISH;end
 else begin tile_groups<=tile_groups+1;channel_batches<=channel_batches+ib;read_bytes<=read_bytes+ird;state<=ADVANCE;end
 end
 ADVANCE:if(!EXTERNAL_COMMIT||group_commit)begin
 if(cg+gco<co)begin cg<=cg+gco;state<=PREP;end
 else begin cg<=0;if(tx+tw<ow)begin tx<=tx+tw;state<=PREP;end
 else begin tx<=0;if(ty+th<oh)begin ty<=ty+th;state<=PREP;end else state<=FINISH;end end end
 FINISH:begin done<=1;state<=IDLE;end
 default:begin failed<=1;error_code<=8;state<=FINISH;end
 endcase end end
endmodule
