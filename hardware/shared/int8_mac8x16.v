`timescale 1ns/1ps
// Eight independent outputs, sixteen signed INT8 products per input beat.
// One vector begins with in_first, ends with in_last. Bias is applied once.
// Global pipeline stall preserves all in-flight vectors under output backpressure.
// Accumulation wraps modulo 2^32; out_overflow reports any signed overflow.
module int8_mac8x16(
 input clk,input reset,
 input in_valid,output in_ready,input in_first,input in_last,
 input [127:0] in_activation,input [1023:0] in_weight,
 input [15:0] in_mask,input [255:0] in_bias,
 output reg out_valid,input out_ready,output reg [255:0] out_result,
 output reg [7:0] out_overflow,output reg protocol_error
);
 wire enable=!out_valid || out_ready;
 assign in_ready=enable;
 reg v1,v2,v3,f1,f2,f3,l1,l2,l3;
 reg [255:0] b1,b2,b3;
 (* use_dsp="yes" *) reg signed [15:0] product[0:127];
 reg signed [17:0] sum4[0:31];
 reg signed [19:0] dot[0:7];
 reg signed [31:0] accumulator[0:7];
 reg [7:0] overflow_acc;
 reg active;
 wire signed [32:0] extended[0:7];
 genvar g;
 generate for(g=0;g<8;g=g+1) begin: results
 wire signed [31:0] initial_value=f3 ? $signed(b3[g*32+:32]):accumulator[g];
 assign extended[g]=$signed({initial_value[31],initial_value})+$signed({{13{dot[g][19]}},dot[g]});
 end endgenerate
 integer o,i,q;
 always @(posedge clk) begin
 if(reset) begin
 v1<=0;v2<=0;v3<=0;f1<=0;f2<=0;f3<=0;l1<=0;l2<=0;l3<=0;b1<=0;b2<=0;b3<=0;
 out_valid<=0;out_result<=0;out_overflow<=0;overflow_acc<=0;active<=0;protocol_error<=0;
 for(i=0;i<128;i=i+1) product[i]<=0;
 for(i=0;i<32;i=i+1) sum4[i]<=0;
 for(i=0;i<8;i=i+1) begin dot[i]<=0;accumulator[i]<=0;end
 end else if(enable) begin
 v1<=in_valid;v2<=v1;v3<=v2;
 f1<=in_first;f2<=f1;f3<=f2;l1<=in_last;l2<=l1;l3<=l2;
 b1<=in_bias;b2<=b1;b3<=b2;
 if(in_valid) for(o=0;o<8;o=o+1) for(i=0;i<16;i=i+1)
 product[o*16+i] <= $signed(in_mask[i] ? in_activation[i*8+:8]:8'd0)*$signed(in_weight[(o*16+i)*8+:8]);
 for(o=0;o<8;o=o+1) begin
 for(q=0;q<4;q=q+1)
 sum4[o*4+q] <= $signed({{2{product[o*16+q*4][15]}},product[o*16+q*4]})+
 $signed({{2{product[o*16+q*4+1][15]}},product[o*16+q*4+1]})+
 $signed({{2{product[o*16+q*4+2][15]}},product[o*16+q*4+2]})+
 $signed({{2{product[o*16+q*4+3][15]}},product[o*16+q*4+3]});
 dot[o] <= $signed({{2{sum4[o*4][17]}},sum4[o*4]})+
 $signed({{2{sum4[o*4+1][17]}},sum4[o*4+1]})+
 $signed({{2{sum4[o*4+2][17]}},sum4[o*4+2]})+
 $signed({{2{sum4[o*4+3][17]}},sum4[o*4+3]});
 end
 out_valid<=v3 && l3;
 if(v3) begin
 if((f3 && active)||(!f3 && !active)) protocol_error<=1;
 active<=!l3;
 for(o=0;o<8;o=o+1) begin
 accumulator[o]<=extended[o][31:0];
 overflow_acc[o]<=(f3 ? 1'b0:overflow_acc[o]) | (extended[o][32]!=extended[o][31]);
 if(l3) begin
 out_result[o*32+:32]<=extended[o][31:0];
 out_overflow[o]<=(f3 ? 1'b0:overflow_acc[o]) | (extended[o][32]!=extended[o][31]);
 end end end
 end end
endmodule
