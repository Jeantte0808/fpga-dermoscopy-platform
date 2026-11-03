`timescale 1ns/1ps
// Up to 4KiB payload staged locally. CRC must pass before any AXI write.
// Caller supplies exclusive AXI ownership from acceptance until busy clears.
// A chunk receipt is not a whole-model parameters_ready certificate.
module parameter_chunk_loader #(parameter WATCHDOG=100000000)(
 input clk,reset,compute_idle,input cmd_valid,output cmd_ready,
 input [29:0] cmd_base,region_base,input [30:0] region_end,
 input [15:0] cmd_bytes,input [31:0] cmd_crc,cmd_tag,
 input payload_valid,output payload_ready,input [127:0] payload_data,input payload_last,
 output busy,output reg done,failed,quarantined,output reg [7:0] error_code,
 output reg [31:0] result_tag,committed_bytes,
 output [29:0] awaddr,output [7:0] awlen,output awvalid,input awready,
 output [127:0] wdata,output wlast,wvalid,input wready,input [1:0] bresp,input bvalid,output bready
);
 localparam IDLE=0,RX=1,HASH=2,CHECK=3,LAUNCH=4,WAIT_DMA=5,FINISH=6,FAULT=7;
 reg [2:0] state;reg [127:0] buffer[0:255];reg [127:0] read_data,hash_word;
 reg [29:0] base;reg [15:0] size;reg [8:0] received,word_count;
 reg [3:0] byte_index;reg [31:0] sum,expected_crc,idle_cycles;
 wire dr,dd,df;wire [7:0] dc;wire [31:0] transferred;wire [7:0] mem_addr;
 function [31:0] crc_byte;input [31:0] crc;input [7:0] data;integer n;reg [31:0] c;begin
 c=crc^data;for(n=0;n<8;n=n+1)c=c[0] ? (c>>1)^32'hedb88320:c>>1;crc_byte=c;end endfunction
 wire [31:0] next_sum=crc_byte(sum,hash_word[7:0]);
 assign busy=state!=IDLE||quarantined;
 assign cmd_ready=state==IDLE&&compute_idle&&!quarantined;
 assign payload_ready=state==RX;
 always @(posedge clk)read_data<=buffer[mem_addr];
 axi_tile_dma_guarded #(.WORD_ADDR_BITS(8),.WATCHDOG(WATCHDOG)) dma(
 .clk(clk),.reset(reset),.cmd_valid(state==LAUNCH),.cmd_ready(dr),.cmd_write(1'b1),.cmd_base(base),.cmd_stride(30'd0),.cmd_row_bytes(size),.cmd_rows(16'd1),
 .done(dd),.failed(df),.error_code(dc),.transferred_bytes(transferred),
 .awaddr(awaddr),.awlen(awlen),.awvalid(awvalid),.awready(awready),.wdata(wdata),.wlast(wlast),.wvalid(wvalid),.wready(wready),.bresp(bresp),.bvalid(bvalid),.bready(bready),
 .arready(1'b0),.rdata(128'd0),.rresp(2'd0),.rlast(1'b0),.rvalid(1'b0),.mem_addr(mem_addr),.mem_rdata(read_data));
 always @(posedge clk)if(reset)begin
 state<=IDLE;done<=0;failed<=0;quarantined<=0;error_code<=0;result_tag<=0;committed_bytes<=0;
 base<=0;size<=0;received<=0;word_count<=0;byte_index<=0;sum<=32'hffffffff;expected_crc<=0;hash_word<=0;idle_cycles<=0;
 end else begin
 done<=0;
 case(state)
 IDLE:if(cmd_valid&&cmd_ready)begin
 result_tag<=cmd_tag;committed_bytes<=0;failed<=0;error_code<=0;received<=0;idle_cycles<=0;sum<=32'hffffffff;
 if(cmd_base[3:0]!=0||region_base[3:0]!=0||region_end[3:0]!=0||cmd_bytes==0||cmd_bytes>4096||cmd_bytes[3:0]!=0||
 cmd_base<region_base||region_end>31'h40000000||({1'b0,cmd_base}+cmd_bytes)>region_end)begin failed<=1;error_code<=6;state<=FINISH;end
 else begin base<=cmd_base;size<=cmd_bytes;word_count<=cmd_bytes>>4;expected_crc<=cmd_crc;state<=RX;end end
 RX:if(payload_valid)begin
 if(payload_last!=(received==word_count-1))begin failed<=1;error_code<=40;state<=FINISH;end
 else begin buffer[received[7:0]]<=payload_data;hash_word<=payload_data;received<=received+1;byte_index<=0;idle_cycles<=0;state<=HASH;end end
 else if(idle_cycles>=WATCHDOG-1)begin failed<=1;error_code<=5;state<=FINISH;end else idle_cycles<=idle_cycles+1;
 HASH:begin sum<=next_sum;hash_word<=hash_word>>8;
 if(byte_index==15)begin if(received==word_count)state<=CHECK;else state<=RX;end else byte_index<=byte_index+1;end
 CHECK:if((sum^32'hffffffff)!=expected_crc)begin failed<=1;error_code<=34;state<=FINISH;end else state<=LAUNCH;
 LAUNCH:if(dr)state<=WAIT_DMA;
 WAIT_DMA:if(dd)begin
 if(df||transferred!=size)begin failed<=1;quarantined<=1;error_code<=df ? dc:12;end
 else committed_bytes<=transferred;
 state<=FINISH;end
 FINISH:begin done<=1;state<=quarantined ? FAULT:IDLE;end
 FAULT:state<=FAULT;
 default:begin failed<=1;quarantined<=1;error_code<=8;state<=FINISH;end
 endcase
 end
endmodule
