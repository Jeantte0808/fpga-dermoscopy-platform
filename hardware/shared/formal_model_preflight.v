`timescale 1ns/1ps
// Verify every parameter/LUT DDR block against the compiled, image-bound table.
// Byte-serial CRC avoids a 128-bit combinational CRC path. R backpressure holds
// each received word while its sixteen bytes are consumed.
module formal_model_preflight #(parameter WATCHDOG=100000)(
 input clk,reset,cmd_valid,output cmd_ready,input invalidate,
 input [255:0] requested_image_id,input [31:0] requested_generation,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready,
 output reg busy,done,failed,verified,output axi_owned,output reg [7:0] error_code,
 output reg [31:0] verified_generation,checked_blocks,checked_bytes
);
 `include "image.vh"
 localparam IDLE=0,FETCH=1,LATCH=2,LOAD=3,WAIT_DATA=4,CHECK=5,ADVANCE=6,FAULT=7;
 reg [2:0] state;reg [11:0] index;
 (* rom_style="block" *) reg [95:0] table_rom[0:IMAGE_CHUNKS-1];reg [95:0] tq;
 initial $readmemh("vectors/chunks.mem",table_rom);
 always @(posedge clk)tq<=table_rom[index];
 reg [29:0] base;reg [15:0] count;reg [31:0] expected_crc,crc_state;
 reg descriptor_done,word_valid;reg [127:0] word_hold;reg [3:0] byte_index;
 wire dr,dd,df,dwe,drr;wire [7:0] de;wire [127:0] mw;wire [31:0] db;
 assign cmd_ready=state==IDLE&&!failed&&!invalidate;
 assign axi_owned=busy||failed;
 assign rready=drr&&!word_valid;
 axi_tile_dma_guarded #(.WORD_ADDR_BITS(8),.WATCHDOG(WATCHDOG)) reader(
 .clk(clk),.reset(reset),.cmd_valid(state==LOAD),.cmd_ready(dr),.cmd_write(1'b0),
 .cmd_base(base),.cmd_stride(30'd0),.cmd_row_bytes(count),.cmd_rows(16'd1),
 .done(dd),.failed(df),.error_code(de),.transferred_bytes(db),.awready(1'b0),.wready(1'b0),.bresp(2'd0),.bvalid(1'b0),
 .araddr(araddr),.arlen(arlen),.arvalid(arvalid),.arready(arready),
 .rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid&&!word_valid),.rready(drr),
 .mem_we(dwe),.mem_wdata(mw),.mem_rdata(128'd0));
 function [31:0] crc_byte;input [31:0] c;input [7:0] b;reg [31:0] v;integer bitno;begin
 v=c^{24'd0,b};for(bitno=0;bitno<8;bitno=bitno+1)v=v[0]?(v>>1)^32'hedb88320:v>>1;crc_byte=v;
 end endfunction
 task fault;input [7:0] code;begin busy<=0;done<=1;failed<=1;verified<=0;error_code<=code;state<=FAULT;end endtask
 always @(posedge clk)begin
 if(reset)begin state<=IDLE;index<=0;base<=0;count<=0;expected_crc<=0;crc_state<=32'hffffffff;
 descriptor_done<=0;word_valid<=0;word_hold<=0;byte_index<=0;busy<=0;done<=0;failed<=0;verified<=0;error_code<=0;verified_generation<=0;checked_blocks<=0;checked_bytes<=0;end
 else begin
 done<=0;
 if(dwe)begin word_hold<=mw;word_valid<=1;byte_index<=0;end
 if(word_valid)begin crc_state<=crc_byte(crc_state,word_hold[byte_index*8+:8]);
 if(byte_index==15)word_valid<=0;else byte_index<=byte_index+1'b1;end
 if(dd)descriptor_done<=1;
 case(state)
 IDLE:if(cmd_valid&&cmd_ready)begin
 verified<=0;checked_blocks<=0;checked_bytes<=0;index<=0;error_code<=0;verified_generation<=requested_generation;
 if(requested_image_id!=IMAGE_ID)fault(21);else begin busy<=1;state<=FETCH;end end
 FETCH:state<=LATCH;
 LATCH:begin
 base<=tq[29:0];count<=tq[47:32];expected_crc<=tq[95:64];crc_state<=32'hffffffff;descriptor_done<=0;
 if(tq[31:30]!=0||tq[63:48]!=0||tq[35:32]!=0||tq[31:0]%16!=0||tq[63:32]==0||tq[63:32]>4096||{1'b0,tq[29:0]}+tq[63:32]>31'h40000000)fault(6);
 else state<=LOAD;end
 LOAD:if(dr)state<=WAIT_DATA;
 WAIT_DATA:if(descriptor_done&&!word_valid)state<=CHECK;
 CHECK:if((crc_state^32'hffffffff)!=expected_crc)fault(20);
 else begin checked_blocks<=checked_blocks+1;checked_bytes<=checked_bytes+db;state<=ADVANCE;end
 ADVANCE:if(index+1==IMAGE_CHUNKS)begin verified<=1;busy<=0;done<=1;state<=IDLE;end
 else begin index<=index+1'b1;state<=FETCH;end
 FAULT:begin end
 default:fault(17);
 endcase
 if(state!=FAULT&&df)fault(de);
 if(invalidate)begin verified<=0;if(state!=IDLE&&state!=FAULT)fault(19);end
 end end
endmodule
