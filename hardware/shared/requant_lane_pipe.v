`timescale 1ns/1ps
// Same DEVELOPMENT integer semantics as requant_scalar, with eight stages.
// Split absolute value, rounding bias, variable shift, sign and zero-point so
// arbitrary per-lane shift/multiplier inputs need not form one long path.
module requant_lane_pipe(
 input clk,reset,in_valid,output in_ready,
 input signed [31:0] in_value,in_multiplier,input [5:0] in_shift,
 input signed [15:0] in_zero_point,input signed [7:0] in_min,in_max,in_residual,
 input in_add_residual,output out_valid,input out_ready,
 output signed [7:0] out_value,output out_error
);
 reg [7:0] valid;wire advance=!valid[7]||out_ready;
 assign in_ready=advance;assign out_valid=valid[7];
 reg signed [63:0] product;reg [64:0] magnitude,biased,shifted;
 reg neg1,neg2,neg3;reg signed [64:0] rounded;
 reg signed [65:0] transformed;reg signed [7:0] scaled,answer;
 reg [5:0] shifts[0:6];reg signed [15:0] zp[0:6];
 reg signed [7:0] lo[0:6],hi[0:6],res[0:6];reg add[0:6],bad[0:7];
 wire signed [8:0] total=$signed(scaled)+(add[6] ? $signed(res[6]):9'sd0);
 assign out_value=answer;assign out_error=bad[7];
 integer j;
 always @(posedge clk)begin
 if(reset)begin valid<=0;product<=0;magnitude<=0;biased<=0;shifted<=0;neg1<=0;neg2<=0;neg3<=0;rounded<=0;transformed<=0;scaled<=0;answer<=0;
 for(j=0;j<7;j=j+1)begin shifts[j]<=0;zp[j]<=0;lo[j]<=0;hi[j]<=0;res[j]<=0;add[j]<=0;bad[j]<=0;end bad[7]<=0;
 end else if(advance)begin
 valid<={valid[6:0],in_valid};
 product<=in_value*in_multiplier;shifts[0]<=in_shift;zp[0]<=in_zero_point;lo[0]<=in_min;hi[0]<=in_max;res[0]<=in_residual;add[0]<=in_add_residual;bad[0]<=in_min>in_max;
 for(j=1;j<7;j=j+1)begin shifts[j]<=shifts[j-1];zp[j]<=zp[j-1];lo[j]<=lo[j-1];hi[j]<=hi[j-1];res[j]<=res[j-1];add[j]<=add[j-1];bad[j]<=bad[j-1];end
 bad[7]<=bad[6];
 magnitude<=product[63] ? {1'b0,~product}+65'd1:{1'b0,product};neg1<=product[63];
 biased<=magnitude+(shifts[1]==0 ? 65'd0:(65'd1<<(shifts[1]-1)));neg2<=neg1;
 shifted<=biased>>shifts[2];neg3<=neg2;
 rounded<=neg3 ? -$signed(shifted):$signed(shifted);
 transformed<=$signed(rounded)+$signed(zp[4]);
 if(transformed<$signed(lo[5]))scaled<=lo[5];else if(transformed>$signed(hi[5]))scaled<=hi[5];else scaled<=transformed[7:0];
 if(total<$signed(lo[6]))answer<=lo[6];else if(total>$signed(hi[6]))answer<=hi[6];else answer<=total[7:0];
 end end
endmodule
