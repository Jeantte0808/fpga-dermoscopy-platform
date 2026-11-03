`timescale 1ns/1ps
// Fixed 256x256 head C16 -> packed HW uint8 Mask through guarded AXI DMA.
// Source is 1MiB, destination 64KiB; disjoint, aligned ranges required.
// Completion means all 4096 writes have successful B responses.
module formal_mask_ddr #(parameter WATCHDOG=100000)(
 input clk,reset,cmd_valid,output cmd_ready,
 input [29:0] cfg_source,cfg_output,
 output [29:0] awaddr,output [7:0] awlen,output awvalid,input awready,
 output [127:0] wdata,output wlast,wvalid,input wready,
 input [1:0] bresp,input bvalid,output bready,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready,
 output reg done,failed,output reg [7:0] error_code,
 output reg [31:0] read_bytes,write_bytes,committed_words
);
 localparam IDLE=0,READ=1,RWAIT=2,SEND=3,PACK=4,WRITE=5,WWAIT=6,FINISH=7,FAULT=8;
 reg [3:0] state;
 reg [29:0] source,dest;
 reg [15:0] pixel;
 reg [127:0] input_word,output_word;
 wire dma_ready,dma_done,dma_failed,mem_we;
 wire [7:0] dma_error;
 wire [31:0] transferred;
 wire [127:0] mem_wdata;
 wire mi_ready,mo_valid,mo_last,mask_failed,frame_done;
 wire [127:0] mo_data;wire [11:0] mo_index;
 wire writing=state==WRITE||state==WWAIT;
 wire [29:0] dma_base=writing ? dest+{committed_words[25:0],4'b0}:source+{10'd0,pixel,4'b0};
 assign cmd_ready=state==IDLE&&!failed;
 formal_mask_pack packer(.clk(clk),.reset(reset),.in_valid(state==SEND),.in_ready(mi_ready),.in_data(input_word),
  .out_valid(mo_valid),.out_ready(state==PACK),.out_data(mo_data),.out_index(mo_index),.out_last(mo_last),.failed(mask_failed),.frame_done(frame_done));
 axi_tile_dma_guarded #(.WORD_ADDR_BITS(1),.WATCHDOG(WATCHDOG)) dma(
  .clk(clk),.reset(reset),.cmd_valid(state==READ||state==WRITE),.cmd_ready(dma_ready),.cmd_write(writing),
  .cmd_base(dma_base),.cmd_stride(30'd16),.cmd_row_bytes(16'd16),.cmd_rows(16'd1),
  .done(dma_done),.failed(dma_failed),.error_code(dma_error),.transferred_bytes(transferred),
  .awaddr(awaddr),.awlen(awlen),.awvalid(awvalid),.awready(awready),.wdata(wdata),.wlast(wlast),.wvalid(wvalid),.wready(wready),
  .bresp(bresp),.bvalid(bvalid),.bready(bready),.araddr(araddr),.arlen(arlen),.arvalid(arvalid),.arready(arready),
  .rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid),.rready(rready),
  .mem_we(mem_we),.mem_wdata(mem_wdata),.mem_rdata(output_word));
 wire invalid_cfg=cfg_source[3:0]!=0||cfg_output[3:0]!=0||
  ({1'b0,cfg_source}+31'd1048576)>31'h40000000||
  ({1'b0,cfg_output}+31'd65536)>31'h40000000||
  (cfg_source<({1'b0,cfg_output}+31'd65536)&&cfg_output<({1'b0,cfg_source}+31'd1048576));
 always @(posedge clk)begin
  if(reset)begin state<=IDLE;source<=0;dest<=0;pixel<=0;input_word<=0;output_word<=0;done<=0;failed<=0;error_code<=0;read_bytes<=0;write_bytes<=0;committed_words<=0;end
  else begin
   done<=0;
   if(mem_we)input_word<=mem_wdata;
   if(state!=FAULT&&(dma_failed||mask_failed))begin
    state<=FAULT;failed<=1;done<=1;error_code<=dma_failed?dma_error:8'd14;
   end else case(state)
    IDLE:if(cmd_valid)begin
     if(invalid_cfg)begin state<=FAULT;failed<=1;done<=1;error_code<=6;end
     else begin source<=cfg_source;dest<=cfg_output;pixel<=0;read_bytes<=0;write_bytes<=0;committed_words<=0;state<=READ;end
    end
    READ:if(dma_ready)state<=RWAIT;
    RWAIT:if(dma_done)begin read_bytes<=read_bytes+16;state<=SEND;end
    SEND:if(mi_ready)begin
     if(pixel[3:0]==15)state<=PACK;
     else begin pixel<=pixel+1'b1;state<=READ;end
    end
    PACK:if(mo_valid)begin
     if(mo_index!=committed_words||mo_last!==(pixel==16'hffff))begin failed<=1;done<=1;error_code<=15;state<=FAULT;end
     else begin output_word<=mo_data;state<=WRITE;end
    end
    WRITE:if(dma_ready)state<=WWAIT;
    WWAIT:if(dma_done)begin
     write_bytes<=write_bytes+16;committed_words<=committed_words+1;
     if(pixel==16'hffff)state<=FINISH;
     else begin pixel<=pixel+1'b1;state<=READ;end
    end
    FINISH:begin done<=1;state<=IDLE;end
    FAULT:state<=FAULT;
    default:begin state<=FAULT;failed<=1;done<=1;error_code<=15;end
   endcase
  end
 end
endmodule
