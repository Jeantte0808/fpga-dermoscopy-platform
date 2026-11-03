`timescale 1ns/1ps
// 16 independent channels, one signed INT8 product per channel per beat.
// Nine beats implement a 3x3 depthwise window; first/last permit other lengths.
// Four-stage globally stalled pipeline, same latency/contract as dense MAC.
module int8_dw16(
 input clk,reset,input in_valid,output in_ready,input in_first,in_last,
 input [127:0] in_activation,in_weight,input [15:0] in_mask,
 input [511:0] in_bias,output reg out_valid,input out_ready,
 output reg [511:0] out_result,output reg [15:0] out_overflow,
 output reg protocol_error
);
 wire enable=!out_valid || out_ready;
 assign in_ready=enable;
 reg v1,v2,v3,f1,f2,f3,l1,l2,l3,active;
 reg [511:0] b1,b2,b3;
 (* use_dsp="yes" *) reg signed [15:0] product[0:15];
 reg signed [15:0] d2[0:15],d3[0:15];
 reg signed [31:0] accumulator[0:15];
 reg [15:0] sticky;
 wire signed [32:0] extended[0:15];
 genvar g;
 generate for(g=0;g<16;g=g+1) begin: channel
 wire signed [31:0] initial_value=f3 ? $signed(b3[g*32+:32]):accumulator[g];
 assign extended[g]=$signed({initial_value[31],initial_value})+$signed({{17{d3[g][15]}},d3[g]});
 end endgenerate
 integer i;
 always @(posedge clk) begin
 if(reset) begin
 v1<=0;v2<=0;v3<=0;f1<=0;f2<=0;f3<=0;l1<=0;l2<=0;l3<=0;active<=0;
 b1<=0;b2<=0;b3<=0;out_valid<=0;out_result<=0;out_overflow<=0;protocol_error<=0;sticky<=0;
 for(i=0;i<16;i=i+1) begin product[i]<=0;d2[i]<=0;d3[i]<=0;accumulator[i]<=0;end
 end else if(enable) begin
 v1<=in_valid;v2<=v1;v3<=v2;f1<=in_first;f2<=f1;f3<=f2;l1<=in_last;l2<=l1;l3<=l2;
 b1<=in_bias;b2<=b1;b3<=b2;out_valid<=v3 && l3;
 for(i=0;i<16;i=i+1) begin
 if(in_valid) product[i]<=$signed(in_mask[i] ? in_activation[i*8+:8]:8'd0)*$signed(in_weight[i*8+:8]);
 d2[i]<=product[i];d3[i]<=d2[i];
 if(v3) begin
 accumulator[i]<=extended[i][31:0];
 sticky[i]<=(f3 ? 1'b0:sticky[i]) | (extended[i][32]!=extended[i][31]);
 if(l3) begin out_result[i*32+:32]<=extended[i][31:0];out_overflow[i]<=(f3 ? 1'b0:sticky[i]) | (extended[i][32]!=extended[i][31]);end
 end end
 if(v3) begin if((f3 && active)||(!f3 && !active)) protocol_error<=1;active<=!l3;end
 end end
endmodule
