`timescale 1ns/1ps
// Transport-neutral header/payload interface. A header is accepted first; the
// sender streams payload ONLY when payload_ready is asserted. Duplicate headers
// return a cached receipt without accepting payload or issuing another AXI write.
module formal_parameter_ingress #(parameter WATCHDOG=100000)(
 input clk,reset,begin_event,input [31:0] begin_generation,
 output begin_ready,input write_allowed,session_owned,
 input cmd_valid,output cmd_ready,input [255:0] cmd_image_id,
 input [31:0] cmd_generation,input [11:0] cmd_index,
 input [15:0] cmd_bytes,input [31:0] cmd_crc,
 input payload_valid,output payload_ready,input [127:0] payload_data,input payload_last,
 output receipt_valid,input receipt_ready,output reg receipt_duplicate,
 output reg [7:0] receipt_error,output reg [11:0] receipt_index,
 output reg [31:0] receipt_generation,receipt_bytes,
 output reg [12:0] next_fragment,output reg [31:0] loaded_bytes,
 output complete,axi_owned,quarantined,output [7:0] fatal_error,
 output [29:0] awaddr,output [7:0] awlen,output awvalid,input awready,
 output [127:0] wdata,output wlast,wvalid,input wready,input [1:0] bresp,input bvalid,output bready
);
 `include "upload.vh"
 localparam IDLE=0,FETCH=1,LATCH=2,LAUNCH=3,WAIT_DMA=4,RECEIPT=5,FAULT=6;
 reg [2:0] state;reg active,duplicate;reg [31:0] generation;
 reg [11:0] index;reg [15:0] count;reg [31:0] expected_crc;reg [255:0] image;
 (* rom_style="block" *) reg [95:0] fragments[0:UPLOAD_FRAGMENTS-1];reg [95:0] tq;
 reg [29:0] base;reg [15:0] size;reg [31:0] crc;
 wire lr,ld,lf,lq,lb;wire [7:0] le;wire [31:0] committed,tag;
 initial $readmemh("vectors/fragments.mem",fragments);
 always @(posedge clk)tq<=fragments[index];
 assign begin_ready=state==IDLE&&!lq;
 assign cmd_ready=state==IDLE&&active&&!lq&&write_allowed&&!session_owned&&!begin_event;
 assign axi_owned=state!=IDLE||lq;
 assign quarantined=lq||state==FAULT;
 assign fatal_error=lq?le:8'd0;
 assign complete=active&&next_fragment==UPLOAD_FRAGMENTS&&!quarantined;
 assign receipt_valid=state==RECEIPT;
 parameter_chunk_loader #(.WATCHDOG(WATCHDOG)) loader(
 .clk(clk),.reset(reset),.compute_idle(1'b1),.cmd_valid(state==LAUNCH),.cmd_ready(lr),
 .cmd_base(base),.region_base(base),.region_end({1'b0,base}+size),.cmd_bytes(size),.cmd_crc(crc),.cmd_tag({20'd0,index}),
 .payload_valid(payload_valid&&state==WAIT_DMA),.payload_ready(payload_ready),.payload_data(payload_data),.payload_last(payload_last),
 .busy(lb),.done(ld),.failed(lf),.quarantined(lq),.error_code(le),.result_tag(tag),.committed_bytes(committed),
 .awaddr(awaddr),.awlen(awlen),.awvalid(awvalid),.awready(awready),.wdata(wdata),.wlast(wlast),.wvalid(wvalid),.wready(wready),.bresp(bresp),.bvalid(bvalid),.bready(bready));
 task reject;input [7:0] code;begin receipt_error<=code;receipt_bytes<=0;receipt_duplicate<=0;state<=RECEIPT;end endtask
 always @(posedge clk)begin
 if(reset)begin state<=IDLE;active<=0;generation<=0;index<=0;count<=0;expected_crc<=0;image<=0;duplicate<=0;
 base<=0;size<=0;crc<=0;receipt_duplicate<=0;receipt_error<=0;receipt_index<=0;receipt_generation<=0;receipt_bytes<=0;next_fragment<=0;loaded_bytes<=0;end
 else case(state)
 IDLE:begin
 if(begin_event)begin active<=1;generation<=begin_generation;next_fragment<=0;loaded_bytes<=0;end
 else if(cmd_valid&&cmd_ready)begin
 index<=cmd_index;count<=cmd_bytes;expected_crc<=cmd_crc;image<=cmd_image_id;
 receipt_index<=cmd_index;receipt_generation<=generation;receipt_bytes<=0;receipt_error<=0;receipt_duplicate<=0;
 duplicate<=next_fragment!=0&&{1'b0,cmd_index}==next_fragment-1'b1;
 if(cmd_generation!=generation)reject(42);
 else if(cmd_index>=UPLOAD_FRAGMENTS||({1'b0,cmd_index}!=next_fragment&&!(next_fragment!=0&&{1'b0,cmd_index}==next_fragment-1'b1)))reject(41);
 else state<=FETCH;end end
 FETCH:state<=LATCH;
 LATCH:begin
 base<=tq[29:0];size<=tq[47:32];crc<=tq[95:64];
 if(image!=UPLOAD_IMAGE_ID)reject(21);
 else if(count!=tq[47:32]||expected_crc!=tq[95:64])reject(43);
 else if(tq[31:30]!=0||tq[63:48]!=0||tq[35:32]!=0||tq[3:0]!=0||tq[63:32]==0||tq[63:32]>1024)reject(6);
 else if(duplicate)begin receipt_duplicate<=1;receipt_bytes<=tq[63:32];state<=RECEIPT;end
 else state<=LAUNCH;end
 LAUNCH:if(lr)state<=WAIT_DMA;
 WAIT_DMA:if(ld)begin
 receipt_error<=le;receipt_bytes<=committed;
 if(lf||tag!={20'd0,index}||committed!=size)begin
 if(!lf)receipt_error<=12;
 end else begin next_fragment<=next_fragment+1'b1;loaded_bytes<=loaded_bytes+committed;end
 state<=RECEIPT;end
 RECEIPT:if(receipt_ready)state<=lq?FAULT:IDLE;
 FAULT:state<=FAULT;
 default:state<=FAULT;
 endcase
 end
endmodule
