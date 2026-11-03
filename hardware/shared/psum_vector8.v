`timescale 1ns/1ps
// Synchronous BRAM partial sums. No reset sweep: first contribution overwrites.
// One vector in flight prevents read/modify/write hazards, even repeated address.
module psum_vector8 #(parameter ADDR_BITS=10)(
 input clk,reset,in_valid,output in_ready,input [ADDR_BITS-1:0] in_address,
 input [255:0] in_value,input [7:0] in_mask,input in_first,in_last,input [63:0] in_tag,
 output out_valid,input out_ready,output [255:0] out_value,output [7:0] out_mask,
 output [63:0] out_tag,output busy,output reg failed,output reg [31:0] contributions,final_vectors
);
 localparam IDLE=0,ADD=1,WRITE=2,OUTPUT=3;
 reg [1:0] state;reg [ADDR_BITS-1:0] address;reg [255:0] partial,previous,sum;
 reg [7:0] mask;reg first,last;reg [63:0] tag;
 (* ram_style="block" *) reg [255:0] memory[0:(1<<ADDR_BITS)-1];
 assign in_ready=state==IDLE&&!failed;assign busy=state!=IDLE;
 assign out_valid=state==OUTPUT&&!failed;assign out_value=sum;assign out_mask=mask;assign out_tag=tag;
 integer c;reg signed [32:0] total;
 always @(posedge clk)begin
 if(reset)begin state<=IDLE;address<=0;partial<=0;previous<=0;sum<=0;mask<=0;first<=0;last<=0;tag<=0;failed<=0;contributions<=0;final_vectors<=0;end
 else case(state)
 IDLE:if(in_valid&&in_ready)begin
 address<=in_address;previous<=memory[in_address];partial<=in_value;mask<=in_mask;first<=in_first;last<=in_last;tag<=in_tag;state<=ADD;
 end
 ADD:begin
 for(c=0;c<8;c=c+1)begin
 total=$signed(partial[c*32+:32])+(first ? 33'sd0:$signed(previous[c*32+:32]));
 if(mask[c])begin sum[c*32+:32]<=total[31:0];if(total[32]!=total[31])failed<=1;end
 else sum[c*32+:32]<=0;
 end state<=WRITE;
 end
 WRITE:if(failed)state<=IDLE;
 else begin memory[address]<=sum;contributions<=contributions+1;state<=last ? OUTPUT:IDLE;end
 OUTPUT:if(out_ready)begin final_vectors<=final_vectors+1;state<=IDLE;end
 default:state<=IDLE;
 endcase
 end
endmodule
