`timescale 1ns/1ps
// UI word stream to the byte-capture side of an actual asynchronous FIFO.
module formal_tx_bytes(input clk,reset,input meta_valid,output meta_ready,input [15:0] meta_length,
 input data_valid,output data_ready,input [127:0] data,input data_last,
 input capture_ready,output capture_start,capture_valid,output [7:0] capture_data,output capture_done,
 output reg [15:0] length,output reg failed);
 localparam IDLE=0,WORD=1,BYTES=2,WAIT_RELEASE=3,FAULT=4;
 reg [2:0] state;reg [127:0] hold_word;reg [3:0] lane;reg [15:0] remaining;
 assign meta_ready=state==IDLE&&capture_ready&&!failed;
 assign capture_start=meta_valid&&meta_ready;assign data_ready=state==WORD&&!failed;
 assign capture_valid=state==BYTES&&!failed;assign capture_data=hold_word[7:0];
 assign capture_done=capture_valid&&lane==15&&remaining==16;
 always @(posedge clk)if(reset)begin state<=IDLE;hold_word<=0;lane<=0;remaining<=0;length<=0;failed<=0;end
 else case(state)
 IDLE:if(capture_start)begin
 length<=meta_length;remaining<=meta_length;
 if(meta_length!=96&&meta_length!=1120)begin failed<=1;state<=FAULT;end else state<=WORD;end
 WORD:if(data_valid)begin
 if(data_last!=(remaining==16))begin failed<=1;state<=FAULT;end
 else begin hold_word<=data;lane<=0;state<=BYTES;end end
 BYTES:begin
 hold_word<=hold_word>>8;lane<=lane+1'b1;
 if(lane==15)begin remaining<=remaining-16;state<=remaining==16?WAIT_RELEASE:WORD;end end
 WAIT_RELEASE:if(capture_ready)state<=IDLE;
 FAULT:state<=FAULT;
 default:begin failed<=1;state<=FAULT;end
 endcase
endmodule
