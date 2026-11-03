`timescale 1ns/1ps
// Development arithmetic: exactly the scalar contract, 16 independent lanes.
// The entire vector and metadata stall together. Not a frozen model format.
module requant_vector16(
 input clk,reset,in_valid,output in_ready,
 input [511:0] in_values,in_multipliers,input [95:0] in_shifts,
 input [255:0] in_zero_points,input [127:0] in_min,in_max,in_residual,
 input [15:0] in_add_residual,in_mask,input [63:0] in_tag,
 output out_valid,input out_ready,output [127:0] out_values,
 output [15:0] out_mask,out_error,output [63:0] out_tag
);
 wire [15:0] lane_valid,lane_ready,lane_error;
 reg [15:0] masks[0:7];reg [63:0] tags[0:7];
 assign in_ready=lane_ready[0];assign out_valid=lane_valid[0];
 assign out_mask=masks[7];assign out_tag=tags[7];
 wire advance=!out_valid||out_ready;
 genvar i;generate for(i=0;i<16;i=i+1)begin: lanes
 wire [7:0] q;
 requant_lane_pipe qlane(.clk(clk),.reset(reset),.in_valid(in_valid),.in_ready(lane_ready[i]),
 .in_value(in_values[i*32+:32]),.in_multiplier(in_multipliers[i*32+:32]),.in_shift(in_shifts[i*6+:6]),
 .in_zero_point(in_zero_points[i*16+:16]),.in_min(in_min[i*8+:8]),.in_max(in_max[i*8+:8]),
 .in_residual(in_residual[i*8+:8]),.in_add_residual(in_add_residual[i]),
 .out_valid(lane_valid[i]),.out_ready(out_ready),.out_value(q),.out_error(lane_error[i]));
 assign out_values[i*8+:8]=out_mask[i] ? q:8'd0;
 assign out_error[i]=out_mask[i]&&lane_error[i];
 end endgenerate
 integer j;
 always @(posedge clk)begin
 if(reset)begin for(j=0;j<8;j=j+1)begin masks[j]<=0;tags[j]<=0;end end
 else if(advance)begin
 masks[0]<=in_mask;tags[0]<=in_tag;
 for(j=1;j<8;j=j+1)begin masks[j]<=masks[j-1];tags[j]<=tags[j-1];end
 end end
endmodule
