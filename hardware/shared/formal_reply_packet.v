`timescale 1ns/1ps
// Buffer a complete reply before exposing it to an Ethernet transmitter.
module formal_reply_packet(input clk,reset,
 input desc_valid,output desc_ready,input [767:0] desc_header,input [15:0] desc_bytes,input [31:0] desc_crc,
 input payload_valid,output payload_ready,input [127:0] payload,input payload_last,
 output meta_valid,input meta_ready,output [15:0] packet_bytes,
 output data_valid,input data_ready,output [127:0] data,output data_last,
 output reg done,failed,output reg [7:0] error_code);
 localparam IDLE=0,RX=1,HASH=2,CHECK=3,HEADER_HASH=4,META=5,HEAD=6,FETCH=7,LOAD=8,BODY=9,FAULT=10;
 reg [3:0] state;reg [767:0] header;reg [15:0] size;reg [31:0] expected_crc,crc;
 reg [6:0] words,received,read_index,sent;reg [3:0] lane;reg [6:0] hbyte;reg [2:0] hword;
 (* ram_style="block" *) reg [127:0] ram[0:63];reg [127:0] hash_word,rq,hold_word;
 function [31:0] crcbyte;input [31:0] c;input [7:0] b;integer j;reg [31:0] t;begin t=c^b;for(j=0;j<8;j=j+1)t=t[0]?(t>>1)^32'hedb88320:t>>1;crcbyte=t;end endfunction
 function [31:0] be32;input [767:0] h;input integer k;begin be32={h[k*8+:8],h[(k+1)*8+:8],h[(k+2)*8+:8],h[(k+3)*8+:8]};end endfunction
 wire [7:0] hb=(hbyte>=72&&hbyte<76)?8'd0:header[hbyte*8+:8];
 wire [31:0] hnext=crcbyte(crc,hb)^32'hffffffff;
 assign desc_ready=state==IDLE&&!failed;assign payload_ready=state==RX&&!failed;
 assign meta_valid=state==META&&!failed;assign packet_bytes=96+size;
 assign data_valid=(state==HEAD||state==BODY)&&!failed;
 assign data=state==HEAD?header[hword*128+:128]:hold_word;
 assign data_last=state==HEAD?(hword==5&&size==0):sent+1==words;
 always @(posedge clk)begin rq<=ram[read_index[5:0]];if(payload_valid&&payload_ready)ram[received[5:0]]<=payload;end
 always @(posedge clk)if(reset)begin state<=IDLE;header<=0;size<=0;expected_crc<=0;crc<=32'hffffffff;
 words<=0;received<=0;read_index<=0;sent<=0;lane<=0;hbyte<=0;hword<=0;hash_word<=0;hold_word<=0;done<=0;failed<=0;error_code<=0;end
 else begin done<=0;case(state)
 IDLE:if(desc_valid)begin
 header<=desc_header;size<=desc_bytes;expected_crc<=desc_crc;crc<=32'hffffffff;words<=desc_bytes>>4;received<=0;read_index<=0;sent<=0;hbyte<=0;hword<=0;
 if((desc_bytes!=0&&desc_bytes!=1024)||be32(desc_header,24)!={16'd0,desc_bytes}||be32(desc_header,28)!=desc_crc||
 (desc_bytes==0&&desc_crc!=0)||(desc_bytes!=0&&(desc_header[5*8+:8]!=6||desc_header[6*8+:8]!=0)))begin failed<=1;error_code<=146;state<=FAULT;end
 else state<=desc_bytes==0?HEADER_HASH:RX;end
 RX:if(payload_valid)begin
 if(payload_last!=(received+1==words))begin failed<=1;error_code<=147;state<=FAULT;end
 else begin received<=received+1'b1;hash_word<=payload;lane<=0;state<=HASH;end end
 HASH:begin crc<=crcbyte(crc,hash_word[7:0]);hash_word<=hash_word>>8;lane<=lane+1'b1;
 if(lane==15)state<=received==words?CHECK:RX;end
 CHECK:if((crc^32'hffffffff)!=expected_crc)begin failed<=1;error_code<=148;state<=FAULT;end
 else begin crc<=32'hffffffff;hbyte<=0;state<=HEADER_HASH;end
 HEADER_HASH:begin crc<=crcbyte(crc,hb);hbyte<=hbyte+1'b1;
 if(hbyte==95)begin header[72*8+:32]<={hnext[7:0],hnext[15:8],hnext[23:16],hnext[31:24]};state<=META;end end
 META:if(meta_ready)state<=HEAD;
 HEAD:if(data_ready)begin
 if(hword==5)begin if(size==0)begin done<=1;state<=IDLE;end else state<=FETCH;end
 else hword<=hword+1'b1;end
 FETCH:state<=LOAD;
 LOAD:begin hold_word<=rq;state<=BODY;end
 BODY:if(data_ready)begin
 sent<=sent+1'b1;
 if(data_last)begin done<=1;state<=IDLE;end else begin read_index<=read_index+1'b1;state<=FETCH;end end
 FAULT:state<=FAULT;
 default:begin failed<=1;error_code<=149;state<=FAULT;end
 endcase end
endmodule
