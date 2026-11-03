`timescale 1ns/1ps
// One retained uint8 Mask packet: header -> 64 stable 128-bit payload beats.
// No Ethernet framing here. Source is the immutable result at 0x40000.
module formal_mask_packet_reader #(parameter WATCHDOG=100000)(
 input clk,reset,cmd_valid,output cmd_ready,input [6:0] cmd_index,
 input [31:0] cmd_frame,cmd_generation,input result_valid,
 input [31:0] result_frame,result_generation,
 output meta_valid,input meta_ready,output reg [7:0] meta_error,
 output reg [6:0] meta_index,output reg [31:0] meta_frame,meta_generation,meta_crc,
 output data_valid,input data_ready,output [127:0] data,output data_last,
 output busy,quarantined,output reg done,done_success,output [7:0] fatal_error,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready
);
 localparam IDLE=0,LOAD=1,WAIT_DMA=2,CHECK=3,META=4,FETCH=5,WAIT_RAM=6,SEND=7,FAULT=8;
 reg [3:0] state;reg q,dma_done,word_valid,binary_bad;reg [31:0] crc;reg [3:0] byte_index;
 reg [127:0] word_hold,read_q,data_hold;reg [5:0] send_index;reg [31:0] stalls;
 (* ram_style="block" *) reg [127:0] buffer[0:63];
 wire dr,dd,df,we,drr;wire [7:0] de;wire [5:0] ma;wire [127:0] mw;wire [31:0] transferred;
 assign cmd_ready=state==IDLE&&!q;assign busy=state!=IDLE||q;assign quarantined=q;assign fatal_error=q?meta_error:8'd0;
 assign meta_valid=state==META;assign data_valid=state==SEND;assign data=data_hold;assign data_last=send_index==63;
 assign rready=drr&&!word_valid;
 always @(posedge clk)begin if(we)buffer[ma]<=mw;read_q<=buffer[send_index];end
 function [31:0] crc8;input [31:0] c;input [7:0] b;integer n;reg [31:0] v;begin
 v=c^b;for(n=0;n<8;n=n+1)v=v[0]?(v>>1)^32'hedb88320:v>>1;crc8=v;end endfunction
 axi_tile_dma_guarded #(.WORD_ADDR_BITS(6),.WATCHDOG(WATCHDOG)) dma(
 .clk(clk),.reset(reset),.cmd_valid(state==LOAD),.cmd_ready(dr),.cmd_write(1'b0),.cmd_base(30'h40000+{meta_index,10'd0}),.cmd_stride(30'd0),.cmd_row_bytes(16'd1024),.cmd_rows(16'd1),
 .done(dd),.failed(df),.error_code(de),.transferred_bytes(transferred),.awready(1'b0),.wready(1'b0),.bresp(2'd0),.bvalid(1'b0),
 .araddr(araddr),.arlen(arlen),.arvalid(arvalid),.arready(arready),.rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid&&!word_valid),.rready(drr),
 .mem_we(we),.mem_addr(ma),.mem_wdata(mw),.mem_rdata(128'd0));
 always @(posedge clk)if(reset)begin state<=IDLE;q<=0;dma_done<=0;word_valid<=0;binary_bad<=0;crc<=32'hffffffff;byte_index<=0;
 word_hold<=0;data_hold<=0;send_index<=0;stalls<=0;meta_error<=0;meta_index<=0;meta_frame<=0;meta_generation<=0;meta_crc<=0;done<=0;done_success<=0;end
 else begin
 done<=0;done_success<=0;
 if(we)begin word_hold<=mw;word_valid<=1;byte_index<=0;end
 if(word_valid)begin
 crc<=crc8(crc,word_hold[byte_index*8+:8]);if(word_hold[byte_index*8+:8]!=0&&word_hold[byte_index*8+:8]!=255)binary_bad<=1;
 if(byte_index==15)word_valid<=0;else byte_index<=byte_index+1'b1;end
 if(dd)dma_done<=1;
 case(state)
 IDLE:if(cmd_valid&&cmd_ready)begin
 meta_error<=0;meta_index<=cmd_index;meta_frame<=result_frame;meta_generation<=result_generation;meta_crc<=0;
 crc<=32'hffffffff;binary_bad<=0;dma_done<=0;send_index<=0;
 if(!result_valid)begin meta_error<=46;state<=META;end
 else if(cmd_generation!=result_generation)begin meta_error<=42;state<=META;end
 else if(cmd_frame!=result_frame)begin meta_error<=45;state<=META;end
 else if(cmd_index>=64)begin meta_error<=41;state<=META;end
 else state<=LOAD;end
 LOAD:if(dr)state<=WAIT_DMA;
 WAIT_DMA:if(dma_done&&!word_valid)state<=CHECK;
 CHECK:begin meta_crc<=crc^32'hffffffff;
 if(binary_bad||transferred!=1024)begin meta_error<=binary_bad?8'd44:8'd12;q<=1;end
 state<=META;end
 META:if(meta_ready)begin if(meta_error!=0)begin done<=1;state<=q?FAULT:IDLE;end else state<=FETCH;end
 FETCH:state<=WAIT_RAM;
 WAIT_RAM:begin data_hold<=read_q;state<=SEND;end
 SEND:if(data_ready)begin if(send_index==63)begin done<=1;done_success<=1;state<=IDLE;end else begin send_index<=send_index+1'b1;state<=FETCH;end end
 FAULT:state<=FAULT;
 default:begin q<=1;meta_error<=17;state<=FAULT;end
 endcase
 // Transport backpressure is unbounded here: keep header/payload stable until
 // consumed or a coordinated reset. Never withdraw an unaccepted valid token.
 if(state!=FAULT&&df)begin q<=1;meta_error<=de;state<=META;end
 end
endmodule
