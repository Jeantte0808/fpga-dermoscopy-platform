`timescale 1ns/1ps
// Single shared DDR port: immutable compiled model upload -> CRC -> real graph.
// External RGB/result DMA and Ethernet framing are deliberately outside this top.
module formal_upload_session #(parameter WATCHDOG=100000)(
 input [6:0] perf_query,output perf_valid,output [511:0] perf_data,output [31:0] perf_frame,perf_generation,
 input clk,reset,external_busy,
 input update_valid,output update_ready,
 input upload_valid,output upload_ready,input [255:0] upload_image_id,
 input [31:0] upload_generation,input [11:0] upload_index,
 input [15:0] upload_bytes,input [31:0] upload_crc,
 input payload_valid,output payload_ready,input [127:0] payload_data,input payload_last,
 output receipt_valid,input receipt_ready,output receipt_duplicate,output [7:0] receipt_error,
 output [11:0] receipt_index,output [31:0] receipt_generation,receipt_bytes,
 output [12:0] next_fragment,output [31:0] loaded_bytes,output upload_complete,
 input verify_valid,output verify_ready,input [255:0] verify_image_id,
 input frame_valid,output frame_ready,input [31:0] frame_id,input result_ready,
 output verify_done,frame_done,result_valid,model_verified,busy,axi_owned,failed,output [7:0] error_code,
 output [31:0] generation,result_frame_id,result_generation,
 output [6:0] stage_index,output [31:0] completed_stages,
 output [29:0] awaddr,output [7:0] awlen,output awvalid,input awready,
 output [127:0] wdata,output wlast,wvalid,input wready,input [1:0] bresp,input bvalid,output bready,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready
);
 wire ubr,sur,allowed,so,uo,uq,sf,sb,svr;wire [7:0] se,ue;
 wire [29:0] ua,sa;wire [7:0] ul,sl;wire uav,sav,uwv,swv,uwl,swl,ubrdy,sbrdy;wire [127:0] uwd,swd;
 wire begin_event=update_valid&&update_ready;
 wire verify_gate=upload_complete&&!uo;
 assign update_ready=sur&&ubr;
 assign verify_ready=svr&&verify_gate;
 assign busy=sb||uo;assign axi_owned=so||uo;
 assign failed=sf||uq;assign error_code=uq?ue:se;
 assign awaddr=uo?ua:sa;assign awlen=uo?ul:sl;assign awvalid=uo?uav:sav;
 assign wdata=uo?uwd:swd;assign wlast=uo?uwl:swl;assign wvalid=uo?uwv:swv;assign bready=uo?ubrdy:sbrdy;
 formal_parameter_ingress #(.WATCHDOG(WATCHDOG)) ingress(
 .clk(clk),.reset(reset),.begin_event(begin_event),.begin_generation(generation==32'hffffffff ? 32'd1:generation+1'b1),
 .begin_ready(ubr),.write_allowed(allowed&&!external_busy),.session_owned(so),
 .cmd_valid(upload_valid),.cmd_ready(upload_ready),.cmd_image_id(upload_image_id),.cmd_generation(upload_generation),.cmd_index(upload_index),.cmd_bytes(upload_bytes),.cmd_crc(upload_crc),
 .payload_valid(payload_valid),.payload_ready(payload_ready),.payload_data(payload_data),.payload_last(payload_last),
 .receipt_valid(receipt_valid),.receipt_ready(receipt_ready),.receipt_duplicate(receipt_duplicate),.receipt_error(receipt_error),.receipt_index(receipt_index),.receipt_generation(receipt_generation),.receipt_bytes(receipt_bytes),
 .next_fragment(next_fragment),.loaded_bytes(loaded_bytes),.complete(upload_complete),.axi_owned(uo),.quarantined(uq),.fatal_error(ue),
 .awaddr(ua),.awlen(ul),.awvalid(uav),.awready(awready&&uo),.wdata(uwd),.wlast(uwl),.wvalid(uwv),.wready(wready&&uo),.bresp(bresp),.bvalid(bvalid&&uo),.bready(ubrdy));
 formal_inference_session #(.WATCHDOG(WATCHDOG)) session(
 .perf_query(perf_query),.perf_valid(perf_valid),.perf_data(perf_data),.perf_frame(perf_frame),.perf_generation(perf_generation),
 .clk(clk),.reset(reset),.configuration_busy(external_busy||uo),.update_valid(update_valid&&ubr),.update_ready(sur),.model_write_allowed(allowed),.model_write_event(uo&&uwv&&wready),
 .verify_valid(verify_valid&&verify_gate),.verify_ready(svr),.verify_image_id(verify_image_id),
 .frame_valid(frame_valid),.frame_ready(frame_ready),.frame_id(frame_id),.result_ready(result_ready),
 .verify_done(verify_done),.frame_done(frame_done),.result_valid(result_valid),.model_verified(model_verified),.busy(sb),.axi_owned(so),.failed(sf),.error_code(se),
 .generation(generation),.result_frame_id(result_frame_id),.result_generation(result_generation),.stage_index(stage_index),.completed_stages(completed_stages),
 .awaddr(sa),.awlen(sl),.awvalid(sav),.awready(awready&&!uo),.wdata(swd),.wlast(swl),.wvalid(swv),.wready(wready&&!uo),.bresp(bresp),.bvalid(bvalid&&!uo),.bready(sbrdy),
 .araddr(araddr),.arlen(arlen),.arvalid(arvalid),.arready(arready&&!uo),.rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid&&!uo),.rready(rready));
endmodule
