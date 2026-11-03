`timescale 1ns/1ps
// Sixteen synchronous parameter RAM banks. One input holding stage, followed
// by the existing eight-stage exact RTA datapath. Configuration is immutable
// until reset; caller may reset this block only after all results drain.
module formal_quant_banked #(parameter MAX_CHANNELS=960)(
 input clk,reset,cfg_valid,output cfg_ready,input [31:0] cfg_multiplier,
 input [7:0] cfg_shift,input signed [7:0] cfg_min,cfg_max,input cfg_commit,
 input [15:0] cfg_channels,output reg configured,cfg_error,
 input in_valid,output in_ready,input [511:0] in_values,input [15:0] in_mask,
 input [15:0] in_channel,input [63:0] in_tag,
 output out_valid,input out_ready,output [127:0] pre_values,
 output reg [127:0] out_values,output [15:0] out_mask,out_error,output [63:0] out_tag
);
 localparam WORDS=(MAX_CHANNELS+15)/16;
 reg [10:0] count;reg [15:0] channels;
 assign cfg_ready=!configured&&!cfg_error&&count<MAX_CHANNELS;
 wire cfg_ok=cfg_channels>0&&cfg_channels<=MAX_CHANNELS&&(count==0||channels==cfg_channels)&&count<(((cfg_channels+15)>>4)<<4)&&
 cfg_multiplier!=0&&!cfg_multiplier[31]&&cfg_shift<=63&&cfg_min!=-128&&cfg_min<=cfg_max&&
 (count<cfg_channels||(cfg_multiplier==1&&cfg_shift==0&&cfg_min==0&&cfg_max==0));
 wire write_parameter=cfg_valid&&cfg_ready&&cfg_ok;
 always @(posedge clk)begin
 if(reset)begin count<=0;channels<=0;configured<=0;cfg_error<=0;end
 else begin
 if(cfg_valid&&cfg_ready)begin
 if(!cfg_ok)cfg_error<=1;
 else begin count<=count+1;channels<=cfg_channels;end end
 if(cfg_commit&&!configured)begin
 if(cfg_channels>0&&cfg_channels<=MAX_CHANNELS&&channels==cfg_channels&&count==(((cfg_channels+15)>>4)<<4)&&!cfg_error&&!cfg_valid)configured<=1;
 else cfg_error<=1;end
 end end
 wire qr;reg bv,pv;wire padvance=!pv||qr;wire badvance=!bv||padvance;
 assign in_ready=badvance&&configured&&!cfg_error;
 wire accept=in_valid&&in_ready;
 reg [511:0] values;reg [15:0] masks,base_channel;reg [63:0] metadata;reg [3:0] rotation;
 always @(posedge clk)begin
 if(reset)begin bv<=0;values<=0;masks<=0;base_channel<=0;metadata<=0;rotation<=0;end
 else if(badvance)begin
 bv<=accept;
 if(accept)begin values<=in_values;masks<=in_mask;base_channel<=in_channel;metadata<=in_tag;rotation<=in_channel[3:0];end
 end end
 wire [53:0] parameter_q[0:15];
 genvar bank;
 generate for(bank=0;bank<16;bank=bank+1)begin: banks
 (* ram_style="block" *) reg [53:0] ram[0:WORDS-1];reg [53:0] q;
 wire [12:0] address={1'b0,in_channel[15:4]}+(bank<in_channel[3:0]);
 always @(posedge clk)begin
 if(write_parameter&&count[3:0]==bank)ram[count[10:4]]<={cfg_max,cfg_min,cfg_shift[5:0],cfg_multiplier};
 if(accept)begin if(address<WORDS)q<=ram[address];else q<=0;end
 end
 assign parameter_q[bank]=q;
 end endgenerate
 reg [511:0] ms;reg [95:0] ss;reg [127:0] lows,highs;reg [15:0] bad;
 reg [53:0] selected;integer i;
 always @*begin
 ms={16{32'd1}};ss=0;lows=0;highs=0;bad=0;selected=0;
 for(i=0;i<16;i=i+1)if(bv&&masks[i])begin
 if(base_channel+i<channels&&base_channel+i<MAX_CHANNELS)begin
 selected=parameter_q[(rotation+i)&15];ms[i*32+:32]=selected[31:0];ss[i*6+:6]=selected[37:32];
 lows[i*8+:8]=selected[45:38];highs[i*8+:8]=selected[53:46];end
 else bad[i]=1;end
 end
 // Register the RAM rotation before the 32x32 multiplier. Without this boundary
 // the rotation mux and DSP carry chain share a critical combinational path.
 reg [511:0] pvalues,pms;reg [95:0] pss;reg [127:0] plows,phighs;reg [15:0] pmasks,pbad;reg [63:0] pmetadata;
 always @(posedge clk)begin
 if(reset)begin pv<=0;pvalues<=0;pms<=0;pss<=0;plows<=0;phighs<=0;pmasks<=0;pbad<=0;pmetadata<=0;end
 else if(padvance)begin pv<=bv;if(bv)begin pvalues<=values;pms<=ms;pss<=ss;plows<=lows;phighs<=highs;pmasks<=masks;pbad<=bad;pmetadata<=metadata;end end
 end
 wire [15:0] lane_error;
 requant_vector16 q(.clk(clk),.reset(reset),.in_valid(pv),.in_ready(qr),
 .in_values(pvalues),.in_multipliers(pms),.in_shifts(pss),.in_zero_points(256'd0),
 .in_min({16{8'h81}}),.in_max({16{8'h7f}}),.in_residual(128'd0),.in_add_residual(16'd0),.in_mask(pmasks),.in_tag(pmetadata),
 .out_valid(out_valid),.out_ready(out_ready),.out_values(pre_values),.out_mask(out_mask),.out_error(lane_error),.out_tag(out_tag));
 reg [127:0] lower[0:7],upper[0:7];reg [15:0] errors[0:7];wire advance=!out_valid||out_ready;integer c;
 assign out_error=lane_error|errors[7];
 always @(posedge clk)begin
 if(reset)for(c=0;c<8;c=c+1)begin lower[c]<=0;upper[c]<=0;errors[c]<=0;end
 else if(advance)begin lower[0]<=plows;upper[0]<=phighs;errors[0]<=pbad;
 for(c=1;c<8;c=c+1)begin lower[c]<=lower[c-1];upper[c]<=upper[c-1];errors[c]<=errors[c-1];end end end
 always @*begin
 out_values=0;
 for(integer j=0;j<16;j=j+1)if(out_mask[j])begin
 if($signed(pre_values[j*8+:8])<$signed(lower[7][j*8+:8]))out_values[j*8+:8]=lower[7][j*8+:8];
 else if($signed(pre_values[j*8+:8])>$signed(upper[7][j*8+:8]))out_values[j*8+:8]=upper[7][j*8+:8];
 else out_values[j*8+:8]=pre_values[j*8+:8];end
 end
endmodule
