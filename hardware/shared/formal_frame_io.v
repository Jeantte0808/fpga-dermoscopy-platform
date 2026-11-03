`timescale 1ns/1ps
// Frame ownership above the transport. RGB is fixed 192x1024 bytes at DDR 0;
// Mask is fixed 64x1024 bytes at DDR 0x40000. All headers carry frame/generation.
module formal_frame_io #(parameter WATCHDOG=100000)(
 input clk,reset,model_verified,input [31:0] model_generation,input core_owned,core_failed,
 input begin_valid,output begin_ready,input [31:0] begin_frame,begin_generation,begin_crc,
 input chunk_valid,output chunk_ready,input [31:0] chunk_frame,chunk_generation,
 input [7:0] chunk_index,input [31:0] chunk_crc,
 input payload_valid,output payload_ready,input [127:0] payload_data,input payload_last,
 output receipt_valid,input receipt_ready,output reg [7:0] receipt_error,receipt_index,
 output reg receipt_duplicate,output [31:0] receipt_frame,receipt_generation,output reg [31:0] receipt_bytes,
 output launch_valid,input launch_ready,output reg [31:0] active_frame,active_generation,
 input result_valid,input [31:0] result_frame,result_generation,
 input get_valid,output get_ready,input [31:0] get_frame,get_generation,input [6:0] get_index,
 output meta_valid,input meta_ready,output [7:0] meta_error,output [6:0] meta_index,
 output [31:0] meta_frame,meta_generation,meta_crc,
 output data_valid,input data_ready,output [127:0] data,output data_last,
 input finish_valid,output finish_ready,input [31:0] finish_frame,finish_generation,
 output [7:0] finish_error,output result_release,
 output frame_locked,io_owned,failed,output reg [7:0] error_code,
 output frame_rejected,output [7:0] input_error,output reg [7:0] next_chunk,
 output reg [63:0] delivered_packets,
 output [29:0] awaddr,output [7:0] awlen,output awvalid,input awready,
 output [127:0] wdata,output wlast,wvalid,input wready,input [1:0] bresp,input bvalid,output bready,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready
);
 localparam EMPTY=0,UPLOAD=1,LOAD=2,WAIT_LOAD=3,RECEIPT=4,CRC_START=5,CRC_WAIT=6,READY=7,COMPUTE=8,RESULT=9,REJECT=10,FAULT=11;
 reg [3:0] state;reg [31:0] frame_crc,packet_crc,last_crc;reg [7:0] index;reg read_owner;
 wire lr,ld,lf,lq,lb;wire [7:0] le;wire [31:0] committed,tag;
 wire cr,cd,cf,cq,cb;wire [7:0] ce;wire [31:0] checked,actual_crc;
 wire mr,md,ms,mq,mb;wire [7:0] me;wire [6:0] mi;
 wire [29:0] ca,ma;wire [7:0] cl,ml;wire cav,mav,crr,mrr;
 assign frame_locked=state!=EMPTY;
 assign io_owned=lb||cb||mb||state==LOAD||state==WAIT_LOAD||state==RECEIPT||state==CRC_START||state==CRC_WAIT;
 assign failed=state==FAULT||lq||cq||mq;
 assign begin_ready=state==EMPTY&&model_verified&&!core_owned&&!core_failed&&!lb&&!cb&&!mb;
 assign chunk_ready=state==UPLOAD&&model_verified&&!core_owned;
 assign receipt_valid=state==RECEIPT;assign receipt_frame=active_frame;assign receipt_generation=active_generation;
 assign launch_valid=state==READY&&model_verified&&active_generation==model_generation;
 assign get_ready=state==RESULT&&mr&&!core_owned;
 assign frame_rejected=state==REJECT;assign input_error=frame_rejected?error_code:8'd0;
 // GET has priority over a simultaneous FINISH. A newly accepted read must
 // acquire its lease before the retained result may be released.
 assign finish_ready=(state==RESULT||state==REJECT)&&!mb&&!core_owned&&!(state==RESULT&&get_valid);
 assign finish_error=finish_generation!=active_generation?8'd42:finish_frame!=active_frame?8'd45:
                     state==RESULT&&delivered_packets!=64'hffffffffffffffff ? 8'd47:8'd0;
 assign result_release=finish_valid&&finish_ready&&finish_error==0&&state==RESULT;
 assign araddr=read_owner?ma:ca;assign arlen=read_owner?ml:cl;assign arvalid=read_owner?mav:cav;assign rready=read_owner?mrr:crr;
 parameter_chunk_loader #(.WATCHDOG(WATCHDOG)) loader(
 .clk(clk),.reset(reset),.compute_idle(1'b1),.cmd_valid(state==LOAD),.cmd_ready(lr),
 .cmd_base({12'd0,index,10'd0}),.region_base(30'd0),.region_end(31'd196608),.cmd_bytes(16'd1024),.cmd_crc(packet_crc),.cmd_tag({24'd0,index}),
 .payload_valid(payload_valid&&state==WAIT_LOAD),.payload_ready(payload_ready),.payload_data(payload_data),.payload_last(payload_last),
 .busy(lb),.done(ld),.failed(lf),.quarantined(lq),.error_code(le),.result_tag(tag),.committed_bytes(committed),
 .awaddr(awaddr),.awlen(awlen),.awvalid(awvalid),.awready(awready),.wdata(wdata),.wlast(wlast),.wvalid(wvalid),.wready(wready),.bresp(bresp),.bvalid(bvalid),.bready(bready));
 formal_buffer_crc #(.WATCHDOG(WATCHDOG)) crc_reader(
 .clk(clk),.reset(reset),.cmd_valid(state==CRC_START),.cmd_ready(cr),.cmd_base(30'd0),.cmd_bytes(32'd196608),.cmd_crc(frame_crc),
 .busy(cb),.done(cd),.failed(cf),.quarantined(cq),.error_code(ce),.checked_bytes(checked),.actual_crc(actual_crc),
 .araddr(ca),.arlen(cl),.arvalid(cav),.arready(arready&&!read_owner),.rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid&&!read_owner),.rready(crr));
 formal_mask_packet_reader #(.WATCHDOG(WATCHDOG)) mask_reader(
 .clk(clk),.reset(reset),.cmd_valid(get_valid&&state==RESULT&&!core_owned),.cmd_ready(mr),.cmd_index(get_index),.cmd_frame(get_frame),.cmd_generation(get_generation),
 .result_valid(result_valid),.result_frame(result_frame),.result_generation(result_generation),
 .meta_valid(meta_valid),.meta_ready(meta_ready),.meta_error(meta_error),.meta_index(meta_index),.meta_frame(meta_frame),.meta_generation(meta_generation),.meta_crc(meta_crc),
 .data_valid(data_valid),.data_ready(data_ready),.data(data),.data_last(data_last),.busy(mb),.quarantined(mq),.done(md),.done_success(ms),.fatal_error(me),
 .araddr(ma),.arlen(ml),.arvalid(mav),.arready(arready&&read_owner),.rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid&&read_owner),.rready(mrr));
 task reject_packet;input [7:0] code;begin receipt_error<=code;receipt_bytes<=0;receipt_duplicate<=0;state<=RECEIPT;end endtask
 always @(posedge clk)if(reset)begin state<=EMPTY;active_frame<=0;active_generation<=0;frame_crc<=0;packet_crc<=0;last_crc<=0;index<=0;read_owner<=0;
 receipt_error<=0;receipt_index<=0;receipt_duplicate<=0;receipt_bytes<=0;error_code<=0;next_chunk<=0;delivered_packets<=0;end
 else begin
 if(md&&ms)delivered_packets[meta_index]<=1;
 case(state)
 EMPTY:if(begin_valid&&begin_ready)begin
 active_frame<=begin_frame;active_generation<=model_generation;frame_crc<=begin_crc;next_chunk<=0;delivered_packets<=0;error_code<=0;
 if(begin_generation!=model_generation)begin error_code<=42;state<=REJECT;end else state<=UPLOAD;end
 UPLOAD:if(chunk_valid&&chunk_ready)begin
 index<=chunk_index;receipt_index<=chunk_index;packet_crc<=chunk_crc;receipt_error<=0;receipt_duplicate<=0;receipt_bytes<=0;
 if(chunk_generation!=active_generation)reject_packet(42);
 else if(chunk_frame!=active_frame)reject_packet(45);
 else if(chunk_index>=192||(chunk_index!=next_chunk&&!(next_chunk!=0&&chunk_index==next_chunk-1'b1)))reject_packet(41);
 else if(next_chunk!=0&&chunk_index==next_chunk-1'b1)begin
 if(chunk_crc!=last_crc)reject_packet(43);
 else begin receipt_duplicate<=1;receipt_bytes<=1024;state<=RECEIPT;end end
 else state<=LOAD;end
 LOAD:if(lr)state<=WAIT_LOAD;
 WAIT_LOAD:if(ld)begin
 receipt_error<=le;receipt_bytes<=committed;
 if(!lf&&committed==1024&&tag==index)begin next_chunk<=next_chunk+1'b1;last_crc<=packet_crc;end
 else if(!lf)begin error_code<=12;state<=FAULT;end
 if(lf||committed==1024&&tag==index)state<=RECEIPT;
 end
 RECEIPT:if(receipt_ready)begin
 if(lq)begin error_code<=le;state<=FAULT;end
 else if(next_chunk==192)begin read_owner<=0;state<=CRC_START;end else state<=UPLOAD;end
 CRC_START:if(cr)state<=CRC_WAIT;
 CRC_WAIT:if(cd)begin
 if(cq)begin error_code<=ce;state<=FAULT;end
 else if(cf||checked!=196608)begin error_code<=cf?ce:8'd12;state<=REJECT;end
 else state<=READY;end
 READY:if(launch_ready&&launch_valid)state<=COMPUTE;
 COMPUTE:if(result_valid)begin
 if(result_frame!=active_frame||result_generation!=active_generation)begin error_code<=24;state<=FAULT;end
 else state<=RESULT;end
 RESULT:begin
 if(get_valid&&get_ready)read_owner<=1;
 if(result_release)state<=EMPTY;
 end
 REJECT:if(finish_valid&&finish_ready&&finish_error==0)state<=EMPTY;
 FAULT:state<=FAULT;
 default:begin error_code<=17;state<=FAULT;end
 endcase
 if(state!=EMPTY&&state!=REJECT&&state!=FAULT&&(!model_verified||model_generation!=active_generation))begin error_code<=19;state<=FAULT;end
 if(core_failed&&state!=FAULT)begin error_code<=19;state<=FAULT;end
 if(mq)begin error_code<=me;state<=FAULT;end
 end
endmodule
