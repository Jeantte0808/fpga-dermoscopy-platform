`timescale 1ns/1ps
// External model upload DMA must obey model_write_allowed and configuration_busy.
// This top supplies no external model_verified override.
module formal_inference_session #(parameter WATCHDOG=100000)(
 input [6:0] perf_query,output perf_valid,output [511:0] perf_data,output [31:0] perf_frame,perf_generation,
 input clk,reset,configuration_busy,
 input update_valid,output update_ready,output model_write_allowed,input model_write_event,
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
 wire owner,pv,pr,pd,pf,pverified,pinvalidate,gv,gr,gd,gf,gresult;
 wire [7:0] pe,ge;wire [31:0] pg,gframe,gactive,ggen;wire [255:0] image;
 wire [29:0] pa,ga,gwa;wire [7:0] pl,gl,gwl;
 wire pav,prr,gav,grr,gwav,gwv,gwlst,gbr;wire [127:0] gwd;
 reg read_pending,write_pending;
 assign araddr=owner?ga:pa;assign arlen=owner?gl:pl;assign arvalid=owner?gav:pav;assign rready=owner?grr:prr;
 assign awaddr=gwa;assign awlen=gwl;assign awvalid=owner&&gwav;
 assign wdata=gwd;assign wlast=gwlst;assign wvalid=owner&&gwv;assign bready=owner&&gbr;
 always @(posedge clk)if(reset)begin read_pending<=0;write_pending<=0;end else begin
 if(arvalid&&arready)read_pending<=1;if(rvalid&&rready&&rlast)read_pending<=0;
 if(awvalid&&awready)write_pending<=1;if(bvalid&&bready)write_pending<=0;end
 formal_inference_manager #(.WATCHDOG(WATCHDOG)) manager(
 .clk(clk),.reset(reset),.configuration_busy(configuration_busy),.update_valid(update_valid),.update_ready(update_ready),.model_write_allowed(model_write_allowed),.model_write_event(model_write_event),
 .verify_valid(verify_valid),.verify_ready(verify_ready),.verify_image_id(verify_image_id),.frame_valid(frame_valid),.frame_ready(frame_ready),.frame_id(frame_id),.result_ready(result_ready),
 .verify_done(verify_done),.frame_done(frame_done),.result_valid(result_valid),.model_verified(model_verified),.busy(busy),.axi_owned(axi_owned),.failed(failed),.error_code(error_code),
 .generation(generation),.result_frame_id(result_frame_id),.result_generation(result_generation),.owner(owner),.read_pending(read_pending),.write_pending(write_pending),
 .preflight_valid(pv),.preflight_ready(pr),.preflight_done(pd),.preflight_failed(pf),.preflight_error(pe),.preflight_verified(pverified),.preflight_generation(pg),.preflight_invalidate(pinvalidate),.requested_image_id(image),
 .graph_valid(gv),.graph_ready(gr),.graph_done(gd),.graph_failed(gf),.graph_error(ge),.graph_result_valid(gresult),.graph_result_frame(gactive),.graph_result_generation(ggen),.graph_frame_id(gframe));
 formal_model_preflight #(.WATCHDOG(WATCHDOG)) preflight(
 .clk(clk),.reset(reset),.cmd_valid(pv),.cmd_ready(pr),.invalidate(pinvalidate),.requested_image_id(image),.requested_generation(generation),
 .araddr(pa),.arlen(pl),.arvalid(pav),.arready(arready&&!owner),.rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid&&!owner),.rready(prr),
 .done(pd),.failed(pf),.verified(pverified),.error_code(pe),.verified_generation(pg));
 formal_graph_engine #(.WATCHDOG(WATCHDOG)) graph(
 .clk(clk),.reset(reset),.cmd_valid(gv),.cmd_ready(gr),.model_verified(model_verified),.model_generation(generation),.frame_id(gframe),
 .awaddr(gwa),.awlen(gwl),.awvalid(gwav),.awready(awready&&owner),.wdata(gwd),.wlast(gwlst),.wvalid(gwv),.wready(wready&&owner),.bresp(bresp),.bvalid(bvalid&&owner),.bready(gbr),
 .araddr(ga),.arlen(gl),.arvalid(gav),.arready(arready&&owner),.rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid&&owner),.rready(grr),
 .done(gd),.failed(gf),.result_valid(gresult),.error_code(ge),.stage_index(stage_index),.completed_stages(completed_stages),.active_frame_id(gactive),.active_generation(ggen));
 formal_perf_counters counters(.clk(clk),.reset(reset),.start(gv&&gr),.stop(gd),.fault(gf),
 .frame(gframe),.generation(generation),.stage(stage_index),
 .arvalid(gav&&owner),.arready(arready),.arlen(gl),.rvalid(rvalid&&owner),.rready(grr),.rlast(rlast),
 .awvalid(gwav&&owner),.awready(awready),.awlen(gwl),.wvalid(gwv&&owner),.wready(wready),.wlast(gwlst),
 .bvalid(bvalid&&owner),.bready(gbr),.query_record(perf_query),.query_valid(perf_valid),.query_data(perf_data),
 .query_frame(perf_frame),.query_generation(perf_generation),.active(),.complete(),.aborted(),.saturated());
endmodule
