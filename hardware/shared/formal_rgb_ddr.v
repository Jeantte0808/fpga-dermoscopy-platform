`timescale 1ns/1ps
// 768-byte authoritative LUT loaded from DDR each frame, followed by RGB888
// 196608-byte raster -> HWC C16 1MiB. Shared guarded DMA, one transaction at a time.
module formal_rgb_ddr #(parameter WATCHDOG=100000)(
 input clk,reset,cmd_valid,output cmd_ready,
 input [29:0] cfg_rgb,cfg_lut,cfg_output,
 output [29:0] awaddr,output [7:0] awlen,output awvalid,input awready,
 output [127:0] wdata,output wlast,wvalid,input wready,
 input [1:0] bresp,input bvalid,output bready,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready,
 output reg done,failed,output reg [7:0] error_code,
 output reg [31:0] read_bytes,write_bytes,committed_pixels
);
 localparam IDLE=0,LR=1,LRWAIT=2,LCFG=3,LCOMMIT=4,LCHECK=5,RR=6,RRWAIT=7,FEED=8,TAKE=9,WRITE=10,WWAIT=11,FINISH=12,FAULT=13;
 reg [3:0] state;reg [29:0] rgb,lut,dest;
 reg [5:0] lut_word;reg [3:0] lane;
 reg [13:0] rgb_words;reg [17:0] sent_bytes;
 reg raw_valid;reg [1:0] component;
 reg [127:0] raw_word,output_word;
 wire writing=state==WRITE||state==WWAIT;
 wire lut_read=state==LR||state==LRWAIT;
 wire [29:0] dma_base=writing?dest+{committed_pixels[25:0],4'b0}:lut_read?lut+{20'd0,lut_word,4'b0}:rgb+{12'd0,rgb_words,4'b0};
 wire dr,dd,df,mwe;wire [7:0] de;wire [127:0] mw;
 wire cfgready,configured,cfgerror,ir,ov,last,ldone,lfail;wire [127:0] od;
 assign cmd_ready=state==IDLE&&!failed;
 // Core is a single-frame transformer. Reset it only on a new idle command;
 // the DMA has no outstanding requests then. LUT is reloaded for that frame.
 wire core_reset=reset||(state==IDLE&&cmd_valid&&cmd_ready);
 formal_rgb_lut converter(.clk(clk),.reset(core_reset),.cfg_valid(state==LCFG),.cfg_data(raw_word[lane*8+:8]),.cfg_commit(state==LCOMMIT),
  .cfg_ready(cfgready),.configured(configured),.cfg_error(cfgerror),.in_valid(state==FEED&&raw_valid),.in_ready(ir),
  .in_data(raw_word[lane*8+:8]),.in_last(sent_bytes==196607),.out_valid(ov),.out_ready(state==TAKE),.out_data(od),.out_last(last),.done(ldone),.failed(lfail));
 axi_tile_dma_guarded #(.WORD_ADDR_BITS(1),.WATCHDOG(WATCHDOG)) dma(.clk(clk),.reset(reset),
  .cmd_valid(state==LR||state==RR||state==WRITE),.cmd_ready(dr),.cmd_write(writing),.cmd_base(dma_base),.cmd_stride(30'd16),.cmd_row_bytes(16'd16),.cmd_rows(16'd1),
  .done(dd),.failed(df),.error_code(de),.awaddr(awaddr),.awlen(awlen),.awvalid(awvalid),.awready(awready),.wdata(wdata),.wlast(wlast),.wvalid(wvalid),.wready(wready),
  .bresp(bresp),.bvalid(bvalid),.bready(bready),.araddr(araddr),.arlen(arlen),.arvalid(arvalid),.arready(arready),.rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid),.rready(rready),
  .mem_we(mwe),.mem_wdata(mw),.mem_rdata(output_word));
 wire bad_cfg=cfg_rgb[3:0]!=0||cfg_lut[3:0]!=0||cfg_output[3:0]!=0||
  ({1'b0,cfg_rgb}+31'd196608)>31'h40000000||({1'b0,cfg_lut}+31'd768)>31'h40000000||({1'b0,cfg_output}+31'd1048576)>31'h40000000||
  (cfg_output<({1'b0,cfg_rgb}+31'd196608)&&cfg_rgb<({1'b0,cfg_output}+31'd1048576))||
  (cfg_output<({1'b0,cfg_lut}+31'd768)&&cfg_lut<({1'b0,cfg_output}+31'd1048576));
 always @(posedge clk)begin
  if(reset)begin state<=IDLE;rgb<=0;lut<=0;dest<=0;lut_word<=0;lane<=0;rgb_words<=0;sent_bytes<=0;raw_valid<=0;component<=0;raw_word<=0;output_word<=0;done<=0;failed<=0;error_code<=0;read_bytes<=0;write_bytes<=0;committed_pixels<=0;end
  else begin
   done<=0;if(mwe)raw_word<=mw;
   if(state!=FAULT&&(df||cfgerror||lfail))begin failed<=1;done<=1;error_code<=df?de:8'd14;state<=FAULT;end
   else case(state)
    IDLE:if(cmd_valid&&cmd_ready)begin
     if(bad_cfg)begin failed<=1;done<=1;error_code<=6;state<=FAULT;end
     else begin rgb<=cfg_rgb;lut<=cfg_lut;dest<=cfg_output;lut_word<=0;lane<=0;rgb_words<=0;sent_bytes<=0;raw_valid<=0;component<=0;read_bytes<=0;write_bytes<=0;committed_pixels<=0;state<=LR;end
    end
    LR:if(dr)state<=LRWAIT;
    LRWAIT:if(dd)begin read_bytes<=read_bytes+16;lane<=0;state<=LCFG;end
    LCFG:if(cfgready)begin
     if(lane==15)begin lane<=0;if(lut_word==47)state<=LCOMMIT;else begin lut_word<=lut_word+1'b1;state<=LR;end end
     else lane<=lane+1'b1;
    end
    LCOMMIT:state<=LCHECK;
    LCHECK:if(configured)state<=RR;
    RR:if(dr)state<=RRWAIT;
    RRWAIT:if(dd)begin read_bytes<=read_bytes+16;rgb_words<=rgb_words+1'b1;lane<=0;raw_valid<=1;state<=FEED;end
    FEED:if(!raw_valid)state<=RR;else if(ir)begin
     sent_bytes<=sent_bytes+1'b1;lane<=lane+1'b1;if(lane==15)raw_valid<=0;
     if(component==2)begin component<=0;state<=TAKE;end else component<=component+1'b1;
    end
    TAKE:if(ov)begin
     if(last!==(committed_pixels==65535))begin failed<=1;done<=1;error_code<=15;state<=FAULT;end
     else begin output_word<=od;state<=WRITE;end
    end
    WRITE:if(dr)state<=WWAIT;
    WWAIT:if(dd)begin
     committed_pixels<=committed_pixels+1;write_bytes<=write_bytes+16;
     if(committed_pixels==65535)state<=FINISH;else state<=FEED;
    end
    FINISH:begin done<=1;state<=IDLE;end
    FAULT:state<=FAULT;
    default:begin failed<=1;done<=1;error_code<=15;state<=FAULT;end
   endcase
  end
 end
endmodule
