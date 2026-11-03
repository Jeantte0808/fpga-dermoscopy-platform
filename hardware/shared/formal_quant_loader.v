`timescale 1ns/1ps
// Load padded per-channel M32/S8/min8/max8 arrays from DDR into an idle
// formal_quant_banked consumer. Shared AXI owner must stay locked until done.
module formal_quant_loader #(parameter WATCHDOG=100000)(
 input clk,reset,cmd_valid,output cmd_ready,
 input [15:0] cfg_channels,input [29:0] cfg_m,cfg_s,cfg_min,cfg_max,
 output param_clear,param_valid,input param_ready,
 output reg [31:0] param_m,output reg [7:0] param_s,param_min,param_max,
 output param_commit,output reg [15:0] param_channels,
 input param_configured,param_error,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready,
 output reg done,failed,output reg [7:0] error_code,
 output reg [31:0] read_bytes,parameters_sent
);
 localparam IDLE=0,CLEAR=1,LOAD=2,LWAIT=3,FETCH=4,EXTRACT=5,UPLOAD=6,COMMIT=7,CHECK=8,FINISH=9,FAULT=10;
 reg [3:0] state;reg [1:0] block_index;
 reg [29:0] bases[0:3];reg [15:0] cp;reg [9:0] channel;
 reg [31:0] wait_cycles;reg hold_fault;
 (* ram_style="block" *) reg [127:0] mram[0:255];
 (* ram_style="block" *) reg [127:0] sram[0:63],lram[0:63],hram[0:63];
 reg [127:0] mq,sq,lq,hq;
 wire dr,dd,df,mwe;wire [7:0] de,ma;wire [127:0] mw;wire [31:0] db;
 wire [15:0] descriptor_bytes=block_index==0 ? cp<<2:cp;
 wire [15:0] cfg_cp=((cfg_channels+15)>>4)<<4;
 assign cmd_ready=state==IDLE&&!failed;
 assign param_clear=state==CLEAR;
 assign param_valid=state==UPLOAD||(state==FAULT&&hold_fault);
 assign param_commit=state==COMMIT;
 axi_tile_dma_guarded #(.WORD_ADDR_BITS(8),.WATCHDOG(WATCHDOG)) reader(
  .clk(clk),.reset(reset),.cmd_valid(state==LOAD),.cmd_ready(dr),.cmd_write(1'b0),.cmd_base(bases[block_index]),.cmd_stride(30'd0),.cmd_row_bytes(descriptor_bytes),.cmd_rows(16'd1),
  .done(dd),.failed(df),.error_code(de),.transferred_bytes(db),.awready(1'b0),.wready(1'b0),.bresp(2'd0),.bvalid(1'b0),
  .araddr(araddr),.arlen(arlen),.arvalid(arvalid),.arready(arready),.rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid),.rready(rready),
  .mem_we(mwe),.mem_addr(ma),.mem_wdata(mw),.mem_rdata(128'd0));
 wire bad_cfg=cfg_channels==0||cfg_channels>960||cfg_m[3:0]!=0||cfg_s[3:0]!=0||cfg_min[3:0]!=0||cfg_max[3:0]!=0||
  {1'b0,cfg_m}+({15'd0,cfg_cp}<<2)>31'h40000000||{1'b0,cfg_s}+cfg_cp>31'h40000000||{1'b0,cfg_min}+cfg_cp>31'h40000000||{1'b0,cfg_max}+cfg_cp>31'h40000000;
 always @(posedge clk)begin
  if(mwe)case(block_index)0:mram[ma]<=mw;1:sram[ma[5:0]]<=mw;2:lram[ma[5:0]]<=mw;3:hram[ma[5:0]]<=mw;endcase
  if(state==FETCH)begin mq<=mram[channel[9:2]];sq<=sram[channel[9:4]];lq<=lram[channel[9:4]];hq<=hram[channel[9:4]];end
  if(reset)begin state<=IDLE;block_index<=0;cp<=0;channel<=0;param_channels<=0;param_m<=0;param_s<=0;param_min<=0;param_max<=0;mq<=0;sq<=0;lq<=0;hq<=0;bases[0]<=0;bases[1]<=0;bases[2]<=0;bases[3]<=0;wait_cycles<=0;hold_fault<=0;done<=0;failed<=0;error_code<=0;read_bytes<=0;parameters_sent<=0;end
  else begin
   done<=0;
   if((state==UPLOAD&&!param_ready)||state==CHECK)wait_cycles<=wait_cycles+1;else wait_cycles<=0;
   if(state!=FAULT&&(df||((state>=FETCH&&state<=CHECK)&&param_error)))begin failed<=1;done<=1;error_code<=df?de:8'd14;state<=FAULT;hold_fault<=state==UPLOAD&&!param_ready;end
   else if(state!=FAULT&&wait_cycles>=WATCHDOG)begin failed<=1;done<=1;error_code<=5;state<=FAULT;hold_fault<=state==UPLOAD&&!param_ready;end
   else case(state)
    IDLE:if(cmd_valid&&cmd_ready)begin
     if(bad_cfg)begin failed<=1;done<=1;error_code<=6;state<=FAULT;end
     else begin param_channels<=cfg_channels;cp<=cfg_cp;bases[0]<=cfg_m;bases[1]<=cfg_s;bases[2]<=cfg_min;bases[3]<=cfg_max;block_index<=0;channel<=0;read_bytes<=0;parameters_sent<=0;state<=CLEAR;end
    end
    CLEAR:state<=LOAD;
    LOAD:if(dr)state<=LWAIT;
    LWAIT:if(dd)begin read_bytes<=read_bytes+db;if(block_index==3)state<=FETCH;else begin block_index<=block_index+1'b1;state<=LOAD;end end
    FETCH:state<=EXTRACT;
    EXTRACT:begin param_m<=mq[channel[1:0]*32+:32];param_s<=sq[channel[3:0]*8+:8];param_min<=lq[channel[3:0]*8+:8];param_max<=hq[channel[3:0]*8+:8];state<=UPLOAD;end
    UPLOAD:if(param_ready)begin parameters_sent<=parameters_sent+1;if(channel+1==cp)state<=COMMIT;else begin channel<=channel+1'b1;state<=FETCH;end end
    COMMIT:state<=CHECK;
    CHECK:if(param_configured)state<=FINISH;
    FINISH:begin done<=1;state<=IDLE;end
    FAULT:if(param_ready)hold_fault<=0;
    default:begin failed<=1;done<=1;error_code<=15;state<=FAULT;end
   endcase
  end
 end
endmodule
