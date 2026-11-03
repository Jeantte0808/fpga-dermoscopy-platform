`timescale 1ns/1ps
// Candidate only. Bounded four-source-pixel tile; unchanged HWC C16 nearest2x.
// All source/destination regions must be disjoint. Guarded DMA retains pending
// AXI signals on fault. Completion is emitted only after the final B response.
module nearest_burst #(parameter WATCHDOG=100000)(
 input clk,reset,cmd_valid,output cmd_ready,
 input [15:0] cfg_h,cfg_w,cfg_ca,cfg_cb,cfg_oh,cfg_ow,cfg_co,
 input [29:0] cfg_a,cfg_output,
 output [29:0] awaddr,output [7:0] awlen,output awvalid,input awready,
 output [127:0] wdata,output wlast,wvalid,input wready,input [1:0] bresp,input bvalid,output bready,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready,
 output reg done,failed,output reg [7:0] error_code,
 output reg [31:0] read_bytes,write_bytes,words_completed,valid_scalars
);
 localparam IDLE=0,GEOM=1,SIZES=2,CHECK=3,TILE=4,BASE=5,READ=6,RWAIT=7,
 FADDR=8,FWAIT=9,FSTORE=10,WRITE=11,WWAIT=12,ADVANCE=13,FINISH=14,FAULT=15,
 PIXEL_BASE=16,BYTE_BASE=17,ADD_BASE=18;
 reg [4:0] state;
 reg [15:0] h,w,ca,cb,oh,ow,co,cp,y,x;
 reg [6:0] groups;
 reg [3:0] tile_pixels,fill_pixel;
 reg [6:0] fill_channel;
 reg [8:0] source_words,output_words,fill_index;
 reg [7:0] fetch_address;
 reg [29:0] ab,ob,read_base,write_base;
 reg dma_writing;
 reg [29:0] dma_base;
 reg [15:0] dma_length;
 reg [31:0] input_size,output_size;
 reg [16:0] input_pixels,output_pixels,source_row,output_row,source_pixel,output_pixel;
 reg [26:0] source_byte,output_byte;
 (* ram_style="block" *) reg [127:0] source_ram[0:255];
 (* ram_style="block" *) reg [127:0] output_ram[0:511];
 reg [127:0] source_q,output_q;
 wire dr,dd,df,mwe;wire [7:0] de;wire [31:0] db;
 wire [8:0] mem_address;wire [127:0] mem_data;
 assign cmd_ready=state==IDLE&&!failed;
 axi_tile_dma_guarded #(.WORD_ADDR_BITS(9),.WATCHDOG(WATCHDOG)) dma(
 .clk(clk),.reset(reset),.cmd_valid(state==READ||state==WRITE),.cmd_ready(dr),.cmd_write(dma_writing),
 .cmd_base(dma_base),.cmd_stride(30'd0),.cmd_row_bytes(dma_length),.cmd_rows(16'd1),
 .done(dd),.failed(df),.error_code(de),.transferred_bytes(db),
 .awaddr(awaddr),.awlen(awlen),.awvalid(awvalid),.awready(awready),
 .wdata(wdata),.wlast(wlast),.wvalid(wvalid),.wready(wready),.bresp(bresp),.bvalid(bvalid),.bready(bready),
 .araddr(araddr),.arlen(arlen),.arvalid(arvalid),.arready(arready),
 .rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid),.rready(rready),
 .mem_we(mwe),.mem_addr(mem_address),.mem_wdata(mem_data),.mem_rdata(output_q));
 reg [127:0] clean_word;integer lane;
 always @* begin
 clean_word=source_q;
 for(integer l=0;l<16;l=l+1)if(fill_channel*16+l>=co)clean_word[l*8+:8]=0;
 end
 always @(posedge clk)begin
 source_q<=source_ram[fetch_address];
 output_q<=output_ram[mem_address];
 if(mwe)source_ram[mem_address[7:0]]<=mem_data;
 if(state==FSTORE)output_ram[fill_index]<=clean_word;
 end
 wire overlap=ob<({1'b0,ab}+input_size)&&ab<({1'b0,ob}+output_size);
 always @(posedge clk)begin
 if(reset)begin
 state<=IDLE;h<=0;w<=0;ca<=0;cb<=0;oh<=0;ow<=0;co<=0;cp<=0;groups<=0;y<=0;x<=0;
 tile_pixels<=0;source_words<=0;output_words<=0;fill_pixel<=0;fill_channel<=0;fill_index<=0;fetch_address<=0;
 ab<=0;ob<=0;read_base<=0;write_base<=0;input_size<=0;output_size<=0;
 dma_writing<=0;dma_base<=0;dma_length<=0;
 input_pixels<=0;output_pixels<=0;source_row<=0;output_row<=0;source_pixel<=0;output_pixel<=0;source_byte<=0;output_byte<=0;
 done<=0;failed<=0;error_code<=0;read_bytes<=0;write_bytes<=0;words_completed<=0;valid_scalars<=0;
 end else begin
 done<=0;
 if(state!=FAULT&&df)begin failed<=1;error_code<=de;done<=1;state<=FAULT;end
 else case(state)
 IDLE:if(cmd_valid&&cmd_ready)begin
 h<=cfg_h;w<=cfg_w;ca<=cfg_ca;cb<=cfg_cb;oh<=cfg_oh;ow<=cfg_ow;co<=cfg_co;ab<=cfg_a;ob<=cfg_output;
 y<=0;x<=0;read_bytes<=0;write_bytes<=0;words_completed<=0;valid_scalars<=0;state<=GEOM;end
 GEOM:begin cp<=((ca+15)>>4)<<4;groups<=(ca+15)>>4;
 input_pixels<=h[8:0]*w[8:0];output_pixels<=oh[8:0]*ow[8:0];state<=SIZES;end
 SIZES:begin input_size<=input_pixels*cp[9:0];output_size<=output_pixels*cp[9:0];state<=CHECK;end
 CHECK:if(h==0||w==0||h>256||w>256||ca==0||ca>960||co!=ca||cb!=0||oh==0||ow==0||oh>256||ow>256||
 oh>2*h||oh+1<2*h||ow>2*w||ow+1<2*w||ab[3:0]!=0||ob[3:0]!=0||
 {1'b0,ab}+input_size>31'h40000000||{1'b0,ob}+output_size>31'h40000000||overlap)
 begin failed<=1;error_code<=6;state<=FINISH;end else state<=TILE;
 TILE:begin tile_pixels<=ow-x>=8 ? 8:ow-x;state<=BASE;end
 BASE:begin
 source_words<=((tile_pixels+1)>>1)*groups;output_words<=tile_pixels*groups;
 source_row<=y[8:1]*w[8:0];output_row<=y[8:0]*ow[8:0];state<=PIXEL_BASE;end
 PIXEL_BASE:begin source_pixel<=source_row+x[8:1];output_pixel<=output_row+x[8:0];state<=BYTE_BASE;end
 BYTE_BASE:begin source_byte<=source_pixel*cp[9:0];output_byte<=output_pixel*cp[9:0];state<=ADD_BASE;end
 ADD_BASE:begin read_base<=ab+source_byte;write_base<=ob+output_byte;
 dma_writing<=0;dma_base<=ab+source_byte;dma_length<={3'd0,source_words,4'd0};state<=READ;end
 READ:if(dr)state<=RWAIT;
 RWAIT:if(dd)begin read_bytes<=read_bytes+db;fill_pixel<=0;fill_channel<=0;fill_index<=0;state<=FADDR;end
 FADDR:begin fetch_address<=(fill_pixel>>1)*groups+fill_channel;state<=FWAIT;end
 FWAIT:state<=FSTORE;
 FSTORE:begin
 if(fill_index+1==output_words)begin
 dma_writing<=1;dma_base<=write_base;dma_length<={3'd0,output_words,4'd0};state<=WRITE;end
 else begin fill_index<=fill_index+1'b1;
 if(fill_channel+1==groups)begin fill_channel<=0;fill_pixel<=fill_pixel+1'b1;end
 else fill_channel<=fill_channel+1'b1;state<=FADDR;end end
 WRITE:if(dr)state<=WWAIT;
 WWAIT:if(dd)begin write_bytes<=write_bytes+db;words_completed<=words_completed+output_words;
 valid_scalars<=valid_scalars+tile_pixels*co;state<=ADVANCE;end
 ADVANCE:if(x+tile_pixels<ow)begin x<=x+tile_pixels;state<=TILE;end
 else if(y+1<oh)begin x<=0;y<=y+1'b1;state<=TILE;end else state<=FINISH;
 FINISH:begin done<=1;state<=IDLE;end
 FAULT:state<=FAULT;
 default:begin failed<=1;error_code<=8;state<=FAULT;done<=1;end
 endcase end end
endmodule
