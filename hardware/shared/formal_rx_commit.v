`timescale 1ns/1ps
// Delay approval until the physical frame has ended and the receiver pipeline
// has settled. Rejected/non-UDP/truncated frames also close the CDC capture.
module formal_rx_commit(input clk,reset,input gmii_dv,gmii_er,
 input packet_done,packet_error,input [15:0] packet_length,input [47:0] packet_mac,input [31:0] packet_ip,input [15:0] packet_port,
 output packet_start,output reg commit_done,commit_error,output reg [15:0] commit_length,commit_port,
 output reg [47:0] commit_mac,output reg [31:0] commit_ip,output reg [31:0] rejected);
 reg previous,active,physical_error;reg [15:0] physical_bytes;reg [3:0] settle;reg [1:0] completions;
 reg result_error;reg [15:0] result_length,result_port;reg [47:0] result_mac;reg [31:0] result_ip;
 assign packet_start=gmii_dv&&!previous;
 wire invalid=physical_error||completions!=1||result_error||result_length<96||result_length>1120||
 {1'b0,physical_bytes}!={1'b0,result_length}+17'd54;
 always @(posedge clk)if(reset)begin
 previous<=0;active<=0;physical_error<=0;physical_bytes<=0;settle<=0;completions<=0;
 result_error<=0;result_length<=0;result_port<=0;result_mac<=0;result_ip<=0;
 commit_done<=0;commit_error<=0;commit_length<=0;commit_port<=0;commit_mac<=0;commit_ip<=0;rejected<=0;
 end else begin
 previous<=gmii_dv;commit_done<=0;
 if(packet_start)begin
 active<=1;physical_bytes<=1;physical_error<=gmii_er;settle<=0;completions<=0;result_error<=0;
 end else if(active)begin
 if(gmii_dv)begin
 if(physical_bytes!=65535)physical_bytes<=physical_bytes+1'b1;else physical_error<=1;
 if(gmii_er)physical_error<=1;
 end else begin
 settle<=settle+1'b1;
 if(settle==7)begin
 commit_done<=1;commit_error<=invalid;commit_length<=result_length;commit_port<=result_port;commit_mac<=result_mac;commit_ip<=result_ip;
 if(invalid)rejected<=rejected+1'b1;active<=0;
 end end
 if(packet_done)begin
 if(completions!=3)completions<=completions+1'b1;
 result_error<=result_error||packet_error;result_length<=packet_length;result_port<=packet_port;result_mac<=packet_mac;result_ip<=packet_ip;
 end
 end
 end
endmodule
