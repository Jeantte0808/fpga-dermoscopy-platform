`timescale 1ns/1ps
// The GMII sender cannot stall. Copy the entire crossed packet before start.
module formal_tx_packet_store(input clk,reset,input meta_valid,output meta_ready,
 input [15:0] meta_length,meta_port,input [47:0] meta_mac,input [31:0] meta_ip,
 input data_valid,output data_ready,input [127:0] data,input [15:0] keep,input data_last,
 output tx_start,input tx_req,tx_done,output [7:0] tx_byte,
 output reg [15:0] tx_length,tx_port,output reg [47:0] tx_mac,output reg [31:0] tx_ip,
 output reg failed,output reg [31:0] transmitted);
 localparam IDLE=0,COPY=1,ARM=2,SEND=3,GAP=4,FAULT=5;
 reg [2:0] state;reg [6:0] write_index,read_index;reg [15:0] remaining,sent;reg [3:0] lane;reg [4:0] gap;
 (* ram_style="block" *) reg [127:0] ram[0:69];reg [127:0] rq;
 assign meta_ready=state==IDLE&&!failed;assign data_ready=state==COPY&&!failed;
 assign tx_start=state==ARM;assign tx_byte=rq[lane*8+:8];
 always @(posedge clk)begin rq<=ram[read_index];if(data_valid&&data_ready&&write_index<70)ram[write_index]<=data;end
 always @(posedge clk)if(reset)begin
 state<=IDLE;write_index<=0;read_index<=0;remaining<=0;sent<=0;lane<=0;gap<=0;
 tx_length<=0;tx_port<=0;tx_mac<=0;tx_ip<=0;failed<=0;transmitted<=0;
 end else case(state)
 IDLE:if(meta_valid)begin
 tx_length<=meta_length;tx_port<=meta_port;tx_mac<=meta_mac;tx_ip<=meta_ip;write_index<=0;read_index<=0;remaining<=meta_length;sent<=0;lane<=0;
 if(meta_length!=96&&meta_length!=1120)begin failed<=1;state<=FAULT;end else state<=COPY;end
 COPY:if(data_valid)begin
 if(keep!=16'hffff||data_last!=(remaining==16)||write_index>=70)begin failed<=1;state<=FAULT;end
 else begin write_index<=write_index+1'b1;remaining<=remaining-16;if(data_last)state<=ARM;end end
 ARM:state<=SEND;
 SEND:begin
 if(tx_req)begin
 sent<=sent+1'b1;lane<=lane+1'b1;
 // Prefetch one cycle before the last byte of the old RAM word is sampled.
 if(lane==14&&sent+2<tx_length)read_index<=read_index+1'b1;
 if(sent>=tx_length)begin failed<=1;state<=FAULT;end
 end
 if(tx_done)begin
 if(sent!=tx_length)begin failed<=1;state<=FAULT;end
 else begin transmitted<=transmitted+1'b1;gap<=0;state<=GAP;end end
 end
 GAP:begin gap<=gap+1'b1;if(gap==19)state<=IDLE;end
 FAULT:state<=FAULT;
 default:begin failed<=1;state<=FAULT;end
 endcase
endmodule
