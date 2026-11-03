`timescale 1ns/1ps
// Byte-serial CRC across an aligned buffer, split into <=4KiB DMA descriptors.
// CRC mismatch is retryable at the frame level; bus faults quarantine to reset.
module formal_buffer_crc #(parameter WATCHDOG=100000)(
 input clk,reset,cmd_valid,output cmd_ready,input [29:0] cmd_base,
 input [31:0] cmd_bytes,cmd_crc,output busy,done,failed,quarantined,
 output reg [7:0] error_code,output reg [31:0] checked_bytes,actual_crc,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready
);
 localparam IDLE=0,LOAD=1,WAIT_DMA=2,ADVANCE=3,FINISH=4,FAULT=5;
 reg [2:0] state;reg fail,q;reg [29:0] base;reg [31:0] remaining,expected,crc;
 reg [15:0] size;reg dma_done,word_valid;reg [127:0] word_hold;reg [3:0] byte_index;
 wire dr,dd,df,we,drr;wire [7:0] de;wire [127:0] word_data;wire [31:0] transferred;
 assign cmd_ready=state==IDLE&&!q;assign busy=state!=IDLE||q;assign done=state==FINISH;
 assign failed=fail;assign quarantined=q;assign rready=drr&&!word_valid;
 function [31:0] crc8;input [31:0] c;input [7:0] b;integer n;reg [31:0] v;begin
 v=c^b;for(n=0;n<8;n=n+1)v=v[0]?(v>>1)^32'hedb88320:v>>1;crc8=v;end endfunction
 axi_tile_dma_guarded #(.WORD_ADDR_BITS(8),.WATCHDOG(WATCHDOG)) dma(
 .clk(clk),.reset(reset),.cmd_valid(state==LOAD),.cmd_ready(dr),.cmd_write(1'b0),.cmd_base(base),.cmd_stride(30'd0),.cmd_row_bytes(size),.cmd_rows(16'd1),
 .done(dd),.failed(df),.error_code(de),.transferred_bytes(transferred),.awready(1'b0),.wready(1'b0),.bresp(2'd0),.bvalid(1'b0),
 .araddr(araddr),.arlen(arlen),.arvalid(arvalid),.arready(arready),.rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid&&!word_valid),.rready(drr),
 .mem_we(we),.mem_wdata(word_data),.mem_rdata(128'd0));
 always @(posedge clk)if(reset)begin
 state<=IDLE;fail<=0;q<=0;base<=0;remaining<=0;expected<=0;crc<=32'hffffffff;size<=0;dma_done<=0;
 word_valid<=0;word_hold<=0;byte_index<=0;error_code<=0;checked_bytes<=0;actual_crc<=0;end
 else begin
 if(we)begin word_hold<=word_data;word_valid<=1;byte_index<=0;end
 if(word_valid)begin crc<=crc8(crc,word_hold[byte_index*8+:8]);if(byte_index==15)word_valid<=0;else byte_index<=byte_index+1'b1;end
 if(dd)dma_done<=1;
 case(state)
 IDLE:if(cmd_valid&&cmd_ready)begin
 fail<=0;error_code<=0;checked_bytes<=0;actual_crc<=0;crc<=32'hffffffff;expected<=cmd_crc;
 base<=cmd_base;remaining<=cmd_bytes;size<=cmd_bytes>4096?16'd4096:cmd_bytes[15:0];dma_done<=0;
 if(cmd_base[3:0]!=0||cmd_bytes==0||cmd_bytes[3:0]!=0||{2'd0,cmd_base}+{1'b0,cmd_bytes}>33'h40000000)begin fail<=1;error_code<=6;state<=FINISH;end
 else state<=LOAD;end
 LOAD:if(dr)state<=WAIT_DMA;
 WAIT_DMA:if(dma_done&&!word_valid)begin checked_bytes<=checked_bytes+transferred;state<=ADVANCE;end
 ADVANCE:if(remaining>size)begin base<=base+size;remaining<=remaining-size;size<=(remaining-size)>4096?16'd4096:remaining-size;dma_done<=0;state<=LOAD;end
 else begin actual_crc<=crc^32'hffffffff;if((crc^32'hffffffff)!=expected)begin fail<=1;error_code<=34;end state<=FINISH;end
 FINISH:state<=q?FAULT:IDLE;
 FAULT:state<=FAULT;
 default:begin fail<=1;q<=1;error_code<=17;state<=FAULT;end
 endcase
 if(state!=FAULT&&df)begin fail<=1;q<=1;error_code<=de;state<=FINISH;end
 end
endmodule
