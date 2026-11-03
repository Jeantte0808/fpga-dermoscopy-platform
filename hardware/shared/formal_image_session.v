`timescale 1ns/1ps
// Transport-neutral system DDR arbitration; no Ethernet parser/CDC yet.
module formal_image_session #(parameter WATCHDOG=100000)(
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
 input image_valid,output image_ready,input [31:0] image_frame,image_generation,image_crc,
 input rgb_valid,output rgb_ready,input [31:0] rgb_frame,rgb_generation,input [7:0] rgb_index,input [31:0] rgb_crc,
 input rgb_payload_valid,output rgb_payload_ready,input [127:0] rgb_payload,input rgb_payload_last,
 output rgb_receipt_valid,input rgb_receipt_ready,output [7:0] rgb_receipt_error,rgb_receipt_index,
 output rgb_receipt_duplicate,output [31:0] rgb_receipt_frame,rgb_receipt_generation,rgb_receipt_bytes,
 input get_valid,output get_ready,input [31:0] get_frame,get_generation,input [6:0] get_index,
 output meta_valid,input meta_ready,output [7:0] meta_error,output [6:0] meta_index,output [31:0] meta_frame,meta_generation,meta_crc,
 output data_valid,input data_ready,output [127:0] data,output data_last,
 input finish_valid,output finish_ready,input [31:0] finish_frame,finish_generation,output [7:0] finish_error,
 output frame_locked,frame_rejected,output [7:0] input_error,next_rgb_chunk,output [63:0] delivered_packets,
 output verify_done,frame_done,result_valid,model_verified,busy,axi_owned,failed,output [7:0] error_code,
 output [31:0] generation,result_frame_id,result_generation,
 output [6:0] stage_index,output [31:0] completed_stages,
 output [29:0] awaddr,output [7:0] awlen,output awvalid,input awready,
 output [127:0] wdata,output wlast,wvalid,input wready,input [1:0] bresp,input bvalid,output bready,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready
);
 wire co,io,cf,ifail,cb,cq,crv,uvr,cvr,ubr,ibr,irr,igr,ifr,lv,lr,release_result,core_frame_ready;
 wire [7:0] ce,ie;wire [31:0] af,ag;
 wire [29:0] ca,ia,cra,ira;wire [7:0] cal,ial,crl,irl;
 wire cav,iav,cwv,iwv,cwl,iwl,cbr,ibrdy,crav,irav,crr,irready;wire [127:0] cwd,iwd;
 wire free_transport=!external_busy&&!cf&&!ifail;
 assign failed=cf||ifail;assign error_code=cf?ce:ie;
 assign model_verified=cq&&!failed;assign result_valid=crv&&!failed;
 assign busy=cb||io;assign axi_owned=co||io||ifail;
 assign update_ready=ubr&&!frame_locked&&!ifail;
 assign verify_ready=cvr&&!frame_locked&&!ifail;
 assign upload_ready=uvr&&!frame_locked&&!ifail;
 assign image_ready=ibr&&!update_valid&&!verify_valid&&free_transport;
 assign rgb_ready=irr&&free_transport;assign get_ready=igr&&free_transport;assign finish_ready=ifr&&free_transport;
 assign awaddr=io?ia:ca;assign awlen=io?ial:cal;assign awvalid=io?iav:cav;
 assign wdata=io?iwd:cwd;assign wlast=io?iwl:cwl;assign wvalid=io?iwv:cwv;assign bready=io?ibrdy:cbr;
 assign araddr=io?ira:cra;assign arlen=io?irl:crl;assign arvalid=io?irav:crav;assign rready=io?irready:crr;
 formal_upload_session #(.WATCHDOG(WATCHDOG)) core(
 .perf_query(perf_query),.perf_valid(perf_valid),.perf_data(perf_data),.perf_frame(perf_frame),.perf_generation(perf_generation),
 .clk(clk),.reset(reset),.external_busy(external_busy||io),
 .update_valid(update_valid&&!frame_locked&&!ifail),.update_ready(ubr),
 .upload_valid(upload_valid&&!frame_locked&&!ifail),.upload_ready(uvr),.upload_image_id(upload_image_id),.upload_generation(upload_generation),.upload_index(upload_index),.upload_bytes(upload_bytes),.upload_crc(upload_crc),
 .payload_valid(payload_valid),.payload_ready(payload_ready),.payload_data(payload_data),.payload_last(payload_last),
 .receipt_valid(receipt_valid),.receipt_ready(receipt_ready),.receipt_duplicate(receipt_duplicate),.receipt_error(receipt_error),.receipt_index(receipt_index),.receipt_generation(receipt_generation),.receipt_bytes(receipt_bytes),
 .next_fragment(next_fragment),.loaded_bytes(loaded_bytes),.upload_complete(upload_complete),
 .verify_valid(verify_valid&&!frame_locked&&!ifail),.verify_ready(cvr),.verify_image_id(verify_image_id),
 .frame_valid(lv&&free_transport),.frame_ready(core_frame_ready),.frame_id(af),.result_ready(release_result),
 .verify_done(verify_done),.frame_done(frame_done),.result_valid(crv),.model_verified(cq),.busy(cb),.axi_owned(co),.failed(cf),.error_code(ce),
 .generation(generation),.result_frame_id(result_frame_id),.result_generation(result_generation),.stage_index(stage_index),.completed_stages(completed_stages),
 .awaddr(ca),.awlen(cal),.awvalid(cav),.awready(awready&&!io),.wdata(cwd),.wlast(cwl),.wvalid(cwv),.wready(wready&&!io),.bresp(bresp),.bvalid(bvalid&&!io),.bready(cbr),
 .araddr(cra),.arlen(crl),.arvalid(crav),.arready(arready&&!io),.rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid&&!io),.rready(crr));

 formal_frame_io #(.WATCHDOG(WATCHDOG)) frame_io(
 .clk(clk),.reset(reset),.model_verified(cq),.model_generation(generation),.core_owned(co),.core_failed(cf),
 .begin_valid(image_valid&&!update_valid&&!verify_valid&&free_transport),.begin_ready(ibr),.begin_frame(image_frame),.begin_generation(image_generation),.begin_crc(image_crc),
 .chunk_valid(rgb_valid&&free_transport),.chunk_ready(irr),.chunk_frame(rgb_frame),.chunk_generation(rgb_generation),.chunk_index(rgb_index),.chunk_crc(rgb_crc),
 .payload_valid(rgb_payload_valid),.payload_ready(rgb_payload_ready),.payload_data(rgb_payload),.payload_last(rgb_payload_last),
 .receipt_valid(rgb_receipt_valid),.receipt_ready(rgb_receipt_ready),.receipt_error(rgb_receipt_error),.receipt_index(rgb_receipt_index),.receipt_duplicate(rgb_receipt_duplicate),.receipt_frame(rgb_receipt_frame),.receipt_generation(rgb_receipt_generation),.receipt_bytes(rgb_receipt_bytes),
 .launch_valid(lv),.launch_ready(core_frame_ready&&free_transport),.active_frame(af),.active_generation(ag),
 .result_valid(crv),.result_frame(result_frame_id),.result_generation(result_generation),
 .get_valid(get_valid&&free_transport),.get_ready(igr),.get_frame(get_frame),.get_generation(get_generation),.get_index(get_index),
 .meta_valid(meta_valid),.meta_ready(meta_ready),.meta_error(meta_error),.meta_index(meta_index),.meta_frame(meta_frame),.meta_generation(meta_generation),.meta_crc(meta_crc),
 .data_valid(data_valid),.data_ready(data_ready),.data(data),.data_last(data_last),
 .finish_valid(finish_valid&&free_transport),.finish_ready(ifr),.finish_frame(finish_frame),.finish_generation(finish_generation),.finish_error(finish_error),.result_release(release_result),
 .frame_locked(frame_locked),.io_owned(io),.failed(ifail),.error_code(ie),.frame_rejected(frame_rejected),.input_error(input_error),.next_chunk(next_rgb_chunk),.delivered_packets(delivered_packets),
 .awaddr(ia),.awlen(ial),.awvalid(iav),.awready(awready&&io),.wdata(iwd),.wlast(iwl),.wvalid(iwv),.wready(wready&&io),.bresp(bresp),.bvalid(bvalid&&io),.bready(ibrdy),
 .araddr(ira),.arlen(irl),.arvalid(irav),.arready(arready&&io),.rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid&&io),.rready(irready));
endmodule
