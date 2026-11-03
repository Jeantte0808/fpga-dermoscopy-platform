`timescale 1ns/1ps
// The input is a complete UDP application packet after the accepted CDC.
// FCP1/FCR1 are isolated candidates; no GMII/PHY/MIG in this top.
module formal_network_session #(parameter WATCHDOG=100000,
 parameter [255:0] IMAGE_ID=256'h9d6ef5d5fee6d9a9ea5b2fd2ba688a3da8a848bde15fc97fdd3615c20867ac89)(
 input clk,reset,input meta_valid,output meta_ready,input [15:0] meta_length,meta_port,input [47:0] meta_mac,input [31:0] meta_ip,
 input data_valid,output data_ready,input [127:0] data,input [15:0] keep,input data_last,
 input [47:0] peer_mac,input [31:0] peer_ip,input [15:0] peer_port,
 output tx_meta_valid,input tx_meta_ready,output [15:0] tx_bytes,
 output tx_valid,input tx_ready,output [127:0] tx_data,output tx_last,
 output failed,output [7:0] error_code,output [31:0] generation,
 output model_verified,result_valid,frame_locked,output [31:0] result_frame_id,result_generation,
 output [31:0] cache_replays,
 output [29:0] awaddr,output [7:0] awlen,output awvalid,input awready,
 output [127:0] wdata,output wlast,wvalid,input wready,input [1:0] bresp,input bvalid,output bready,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready);
 `include "core_wires.vh"
 localparam IDLE=0,DISPATCH=1,UPDATE_FIRE=2,UPDATE_ACK=3,UPLOAD_FIRE=4,UPLOAD_WAIT=5,
 VERIFY_FIRE=6,VERIFY_WAIT=7,IMAGE_FIRE=8,RGB_FIRE=9,RGB_WAIT=10,GET_FIRE=11,GET_WAIT=12,
 FINISH_FIRE=13,DRAIN=14,BUILD=15,DESC=16,GET_PAYLOAD=17,WAIT_TX=18,PERF_WAIT0=19,PERF_WAIT1=20,PERF_WAIT2=21,PERF_CAPTURE=22;
 reg [4:0] state;reg [7:0] op,request_error,response_error;
 reg [31:0] txn,gen,frame,index,chunk_crc,frame_crc,reply_gen,reply_frame,reply_index,reply_crc;
 reg [15:0] bytes,reply_bytes;reg body_done,duplicate,replaying,cache_valid,cache_eligible;
 reg [471:0] fingerprint,cache_fingerprint;reg [767:0] reply_header,cache_header;
 reg [31:0] last_txn,last_frame,replays;
 wire perf_request=op==8&&index[31:16]==16'h5052;
 reg [511:0] perf_snapshot;reg perf_reply;
 assign c_perf_query=index[10:4];
 wire qv,qr,pv,pr,pl,pbusy,pfailed;wire [7:0] qe,qop,pe;
 wire [31:0] qt,qg,qf,qi,qc,qfc;wire [15:0] qb;wire [255:0] qimage;wire [127:0] pd;
 wire [471:0] qfingerprint={qop,qt,qg,qf,qi,qb,qc,qfc,qimage};
 wire desc_ready,reply_payload_ready,tx_done,tx_failed;wire [7:0] tx_error;
 wire mutating=op==1||op==2||op==3||op==4||op==5||op==7;
 wire body_handshake=pv&&pr;
 assign qr=state==IDLE;
 assign pr=state==DRAIN||(state==UPLOAD_WAIT&&c_payload_ready)||(state==RGB_WAIT&&c_rgb_payload_ready);
 assign failed=c_failed||pfailed||tx_failed;
 assign error_code=pfailed?pe:tx_failed?tx_error:c_error_code;
 assign generation=c_generation;assign model_verified=c_model_verified;assign result_valid=c_result_valid;
 assign frame_locked=c_frame_locked;assign result_frame_id=c_result_frame_id;assign result_generation=c_result_generation;assign cache_replays=replays;
 formal_command_rx #(.IMAGE_ID(IMAGE_ID),.WATCHDOG(WATCHDOG)) parser(
 .clk(clk),.reset(reset),.meta_valid(meta_valid),.meta_ready(meta_ready),.meta_length(meta_length),.meta_port(meta_port),.meta_mac(meta_mac),.meta_ip(meta_ip),
 .data_valid(data_valid),.data_ready(data_ready),.data(data),.keep(keep),.data_last(data_last),.peer_mac(peer_mac),.peer_ip(peer_ip),.peer_port(peer_port),
 .req_valid(qv),.req_ready(qr),.req_error(qe),.req_opcode(qop),.req_txn(qt),.req_generation(qg),.req_frame(qf),.req_index(qi),.req_bytes(qb),.req_crc(qc),.req_frame_crc(qfc),.req_image_id(qimage),
 .payload_valid(pv),.payload_ready(pr),.payload(pd),.payload_last(pl),.busy(pbusy),.failed(pfailed),.fatal_error(pe));
 assign c_clk=clk;assign c_reset=reset;assign c_external_busy=pfailed||tx_failed;
 assign c_update_valid=state==UPDATE_FIRE;
 assign c_upload_valid=state==UPLOAD_FIRE;assign c_upload_image_id=IMAGE_ID;assign c_upload_generation=gen;assign c_upload_index=index[11:0];assign c_upload_bytes=bytes;assign c_upload_crc=chunk_crc;
 assign c_payload_valid=pv&&state==UPLOAD_WAIT;assign c_payload_data=pd;assign c_payload_last=pl;assign c_receipt_ready=state==UPLOAD_WAIT;
 assign c_verify_valid=state==VERIFY_FIRE;assign c_verify_image_id=IMAGE_ID;
 assign c_image_valid=state==IMAGE_FIRE;assign c_image_frame=frame;assign c_image_generation=gen;assign c_image_crc=frame_crc;
 assign c_rgb_valid=state==RGB_FIRE;assign c_rgb_frame=frame;assign c_rgb_generation=gen;assign c_rgb_index=index[7:0];assign c_rgb_crc=chunk_crc;
 assign c_rgb_payload_valid=pv&&state==RGB_WAIT;assign c_rgb_payload=pd;assign c_rgb_payload_last=pl;assign c_rgb_receipt_ready=state==RGB_WAIT;
 assign c_get_valid=state==GET_FIRE;assign c_get_frame=frame;assign c_get_generation=gen;assign c_get_index=index[6:0];assign c_meta_ready=state==GET_WAIT;
 assign c_data_ready=state==GET_PAYLOAD&&reply_payload_ready;
 assign c_finish_valid=state==FINISH_FIRE;assign c_finish_frame=frame;assign c_finish_generation=gen;
 assign awaddr=c_awaddr;assign awlen=c_awlen;assign awvalid=c_awvalid;assign c_awready=awready;
 assign wdata=c_wdata;assign wlast=c_wlast;assign wvalid=c_wvalid;assign c_wready=wready;assign c_bresp=bresp;assign c_bvalid=bvalid;assign bready=c_bready;
 assign araddr=c_araddr;assign arlen=c_arlen;assign arvalid=c_arvalid;assign c_arready=arready;
 assign c_rdata=rdata;assign c_rresp=rresp;assign c_rlast=rlast;assign c_rvalid=rvalid;assign rready=c_rready;
 `include "core_instance.vh"
 formal_reply_packet reply(.clk(clk),.reset(reset),.desc_valid(state==DESC),.desc_ready(desc_ready),.desc_header(reply_header),.desc_bytes(reply_bytes),.desc_crc(reply_crc),
 .payload_valid(state==GET_PAYLOAD&&c_data_valid),.payload_ready(reply_payload_ready),.payload(c_data),.payload_last(c_data_last),
 .meta_valid(tx_meta_valid),.meta_ready(tx_meta_ready),.packet_bytes(tx_bytes),.data_valid(tx_valid),.data_ready(tx_ready),.data(tx_data),.data_last(tx_last),.done(tx_done),.failed(tx_failed),.error_code(tx_error));
 function [31:0] network32;input [31:0] v;begin network32={v[7:0],v[15:8],v[23:16],v[31:24]};end endfunction
 reg [767:0] header_now;integer h;
 always @* begin
 header_now=0;header_now[0+:32]=32'h31524346;header_now[4*8+:8]=1;header_now[5*8+:8]=op;header_now[6*8+:8]=response_error;
 header_now[7*8+:8]={3'd0,c_failed,c_frame_rejected,c_result_valid,c_model_verified,duplicate};
 header_now[8*8+:32]=network32(txn);header_now[12*8+:32]=network32(reply_gen);header_now[16*8+:32]=network32(reply_frame);header_now[20*8+:32]=network32(reply_index);
 header_now[24*8+:32]=network32({16'd0,reply_bytes});header_now[28*8+:32]=network32(reply_crc);
 header_now[32*8+:32]=network32({24'd0,c_input_error});header_now[36*8+:32]=network32(c_completed_stages);
 for(h=0;h<32;h=h+1)header_now[(40+h)*8+:8]=IMAGE_ID[255-h*8-:8];
 if(perf_reply)begin
 header_now[32*8+:32]=network32(perf_snapshot[480+:32]);
 header_now[76*8+:32]=network32(perf_snapshot[(index[3:0]*5+0)*32+:32]);
 header_now[80*8+:32]=network32(perf_snapshot[(index[3:0]*5+1)*32+:32]);
 header_now[84*8+:32]=network32(perf_snapshot[(index[3:0]*5+2)*32+:32]);
 header_now[88*8+:32]=network32(perf_snapshot[(index[3:0]*5+3)*32+:32]);
 header_now[92*8+:32]=network32(perf_snapshot[(index[3:0]*5+4)*32+:32]);
 end else begin
 header_now[76*8+:32]=network32(c_loaded_bytes);header_now[80*8+:32]=network32({19'd0,c_next_fragment});header_now[84*8+:32]=network32({24'd0,c_next_rgb_chunk});
 header_now[88*8+:32]=network32(c_result_frame_id);header_now[92*8+:32]=network32(c_result_generation);end
 end
 task reject_request;input [7:0] e;begin response_error<=e;reply_bytes<=0;reply_crc<=0;state<=body_done?BUILD:DRAIN;end endtask
 always @(posedge clk)if(reset)begin
 perf_snapshot<=0;perf_reply<=0;state<=IDLE;op<=0;request_error<=0;response_error<=0;txn<=0;gen<=0;frame<=0;index<=0;chunk_crc<=0;frame_crc<=0;bytes<=0;
 reply_gen<=0;reply_frame<=0;reply_index<=0;reply_crc<=0;reply_bytes<=0;body_done<=1;duplicate<=0;replaying<=0;cache_valid<=0;cache_eligible<=0;
 fingerprint<=0;cache_fingerprint<=0;reply_header<=0;cache_header<=0;last_txn<=0;last_frame<=0;replays<=0;
 end else begin
 if(body_handshake&&pl)body_done<=1;
 case(state)
 IDLE:if(qv&&qr)begin
 perf_reply<=0;op<=qop;txn<=qt;gen<=qg;frame<=qf;index<=qi;bytes<=qb;chunk_crc<=qc;frame_crc<=qfc;request_error<=qe;fingerprint<=qfingerprint;
 reply_gen<=c_generation;reply_frame<=qf;reply_index<=qi;reply_bytes<=0;reply_crc<=0;response_error<=0;duplicate<=0;replaying<=0;
 cache_eligible<=qe==0&&(qop==1||qop==2||qop==3||qop==4||qop==5||qop==7);
 body_done<=qe!=0||qb==0;state<=DISPATCH;end
 DISPATCH:begin
 if(request_error!=0)reject_request(request_error);
 else if(cache_valid&&fingerprint==cache_fingerprint)begin
 replaying<=1;reply_header<=cache_header;reply_bytes<=0;reply_crc<=0;replays<=replays+1'b1;state<=body_done?DESC:DRAIN;end
 else if(op==8)state<=perf_request?PERF_WAIT0:BUILD;
 else if(mutating&&txn<=last_txn)reject_request(137);
 else if(gen!=c_generation)reject_request(138);
 else if(c_failed)reject_request(c_error_code);
 else case(op)
 1:if(c_generation==32'hffffffff)reject_request(142);else if(c_update_ready)state<=UPDATE_FIRE;else reject_request(136);
 2:if(c_upload_ready)state<=UPLOAD_FIRE;else reject_request(136);
 3:if(c_verify_ready)state<=VERIFY_FIRE;else reject_request(136);
 4:if(frame<=last_frame)reject_request(139);else if(c_image_ready)state<=IMAGE_FIRE;else reject_request(136);
 5:if(c_rgb_ready)state<=RGB_FIRE;else reject_request(136);
 6:if(frame!=last_frame)reject_request(141);else if(c_frame_rejected)reject_request(c_input_error);else if(c_get_ready)state<=GET_FIRE;else reject_request(136);
 7:if(c_finish_ready)state<=FINISH_FIRE;else reject_request(136);
 default:reject_request(134);
 endcase end
 PERF_WAIT0:state<=PERF_WAIT1;
 PERF_WAIT1:state<=PERF_WAIT2;
 PERF_WAIT2:state<=PERF_CAPTURE;
 PERF_CAPTURE:if(!c_perf_valid||c_perf_generation!=c_generation)reject_request(136);
 else begin perf_reply<=1;perf_snapshot<=c_perf_data;reply_frame<=c_perf_frame;reply_gen<=c_perf_generation;state<=BUILD;end
 UPDATE_FIRE:if(c_update_ready)begin last_frame<=0;state<=UPDATE_ACK;end
 UPDATE_ACK:begin reply_gen<=c_generation;state<=BUILD;end
 UPLOAD_FIRE:if(c_upload_ready)state<=UPLOAD_WAIT;
 UPLOAD_WAIT:if(c_receipt_valid)begin
 response_error<=c_receipt_error;duplicate<=c_receipt_duplicate;reply_gen<=c_receipt_generation;reply_index<={20'd0,c_receipt_index};
 state<=body_done||(body_handshake&&pl)?BUILD:DRAIN;end
 VERIFY_FIRE:if(c_verify_ready)state<=VERIFY_WAIT;
 VERIFY_WAIT:if(c_failed)reject_request(c_error_code);else if(c_verify_done)begin if(!c_model_verified)response_error<=23;state<=BUILD;end
 IMAGE_FIRE:if(c_image_ready)begin last_frame<=frame;state<=BUILD;end
 RGB_FIRE:if(c_rgb_ready)state<=RGB_WAIT;
 RGB_WAIT:if(c_rgb_receipt_valid)begin
 response_error<=c_rgb_receipt_error;duplicate<=c_rgb_receipt_duplicate;reply_gen<=c_rgb_receipt_generation;reply_frame<=c_rgb_receipt_frame;reply_index<={24'd0,c_rgb_receipt_index};
 state<=body_done||(body_handshake&&pl)?BUILD:DRAIN;end
 GET_FIRE:if(c_get_ready)state<=GET_WAIT;
 GET_WAIT:if(c_meta_valid)begin
 response_error<=c_meta_error;reply_gen<=c_meta_generation;reply_frame<=c_meta_frame;reply_index<={25'd0,c_meta_index};
 reply_bytes<=c_meta_error==0?16'd1024:16'd0;reply_crc<=c_meta_error==0?c_meta_crc:32'd0;state<=BUILD;end
 FINISH_FIRE:if(c_finish_ready)begin response_error<=c_finish_error;state<=BUILD;end
 DRAIN:if(body_handshake&&pl)state<=replaying?DESC:BUILD;
 BUILD:begin
 reply_header<=header_now;
 if(cache_eligible&&response_error!=136&&txn>last_txn)begin cache_valid<=1;cache_fingerprint<=fingerprint;cache_header<=header_now;last_txn<=txn;end
 state<=DESC;end
 DESC:if(desc_ready)state<=reply_bytes==0?WAIT_TX:GET_PAYLOAD;
 GET_PAYLOAD:if(c_data_valid&&c_data_ready&&c_data_last)state<=WAIT_TX;
 WAIT_TX:if(tx_done)state<=IDLE;
 default:state<=IDLE;
 endcase end
endmodule
