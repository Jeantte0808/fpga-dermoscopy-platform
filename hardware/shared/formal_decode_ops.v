`timescale 1ns/1ps
// Formal op3: per-branch requantization, logical-channel Concat, zero C16 tails.
// Op4: exact nearest2x copy. Each written word waits for its B response.
// Op2 is rejected: formal residual Add has a separate verified implementation.
module formal_decode_ops #(parameter WATCHDOG=100000)(
 input clk,reset,cmd_valid,output cmd_ready,input [2:0] cfg_op,
 input [31:0] cfg_ma,cfg_mb,input [7:0] cfg_sa,cfg_sb,input cfg_bpa,cfg_bpb,
 input [15:0] cfg_h,cfg_w,cfg_ca,cfg_bh,cfg_bw,cfg_cb,cfg_oh,cfg_ow,cfg_co,
 input [29:0] cfg_a,cfg_b,cfg_output,
 output [29:0] awaddr,output [7:0] awlen,output awvalid,input awready,
 output [127:0] wdata,output wlast,wvalid,input wready,input [1:0] bresp,input bvalid,output bready,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready,
 output done,failed,output [7:0] error_code,
 output [31:0] read_bytes,write_bytes,words_completed,valid_scalars,
 output aligned_valid,output [29:0] aligned_address,output [127:0] aligned_values
);
 // Selection is latched for the complete command, including delayed B and faults.
 reg active,chosen;
 wire select_fast=active ? chosen:(cfg_op==4);
 always @(posedge clk)if(reset)begin active<=0;chosen<=0;end
 else if(cmd_valid&&cmd_ready)begin active<=1;chosen<=cfg_op==4;end
 else if(active&&done&&!failed)active<=0;
 wire a_cmd_ready,b_cmd_ready;
 assign cmd_ready=!active&&((select_fast ? b_cmd_ready:a_cmd_ready));
 wire [29:0] a_awaddr,b_awaddr;
 assign awaddr=(select_fast ? b_awaddr:a_awaddr);
 wire [7:0] a_awlen,b_awlen;
 assign awlen=(select_fast ? b_awlen:a_awlen);
 wire a_awvalid,b_awvalid;
 assign awvalid=(select_fast ? b_awvalid:a_awvalid);
 wire [127:0] a_wdata,b_wdata;
 assign wdata=(select_fast ? b_wdata:a_wdata);
 wire a_wlast,b_wlast;
 assign wlast=(select_fast ? b_wlast:a_wlast);
 wire a_wvalid,b_wvalid;
 assign wvalid=(select_fast ? b_wvalid:a_wvalid);
 wire a_bready,b_bready;
 assign bready=(select_fast ? b_bready:a_bready);
 wire [29:0] a_araddr,b_araddr;
 assign araddr=(select_fast ? b_araddr:a_araddr);
 wire [7:0] a_arlen,b_arlen;
 assign arlen=(select_fast ? b_arlen:a_arlen);
 wire a_arvalid,b_arvalid;
 assign arvalid=(select_fast ? b_arvalid:a_arvalid);
 wire a_rready,b_rready;
 assign rready=(select_fast ? b_rready:a_rready);
 wire a_done,b_done;
 assign done=(select_fast ? b_done:a_done);
 wire a_failed,b_failed;
 assign failed=(select_fast ? b_failed:a_failed);
 wire [7:0] a_error_code,b_error_code;
 assign error_code=(select_fast ? b_error_code:a_error_code);
 wire [31:0] a_read_bytes,b_read_bytes;
 assign read_bytes=(select_fast ? b_read_bytes:a_read_bytes);
 wire [31:0] a_write_bytes,b_write_bytes;
 assign write_bytes=(select_fast ? b_write_bytes:a_write_bytes);
 wire [31:0] a_words_completed,b_words_completed;
 assign words_completed=(select_fast ? b_words_completed:a_words_completed);
 wire [31:0] a_valid_scalars,b_valid_scalars;
 assign valid_scalars=(select_fast ? b_valid_scalars:a_valid_scalars);
 wire legacy_aligned_valid;
 assign aligned_valid=!select_fast&&legacy_aligned_valid;
 wire [29:0] legacy_aligned_address;
 assign aligned_address=select_fast ? 30'd0:legacy_aligned_address;
 wire [127:0] legacy_aligned_values;
 assign aligned_values=select_fast ? 128'd0:legacy_aligned_values;
 formal_decode_legacy #(.WATCHDOG(WATCHDOG)) a_engine(
 .clk(clk),
 .reset(reset),
 .awready(awready),
 .wready(wready),
 .bresp(bresp),
 .bvalid(bvalid),
 .arready(arready),
 .rdata(rdata),
 .rresp(rresp),
 .rlast(rlast),
 .rvalid(rvalid),
 .cfg_op(cfg_op),
 .cfg_ma(cfg_ma),
 .cfg_mb(cfg_mb),
 .cfg_sa(cfg_sa),
 .cfg_sb(cfg_sb),
 .cfg_bpa(cfg_bpa),
 .cfg_bpb(cfg_bpb),
 .cfg_h(cfg_h),
 .cfg_w(cfg_w),
 .cfg_ca(cfg_ca),
 .cfg_bh(cfg_bh),
 .cfg_bw(cfg_bw),
 .cfg_cb(cfg_cb),
 .cfg_oh(cfg_oh),
 .cfg_ow(cfg_ow),
 .cfg_co(cfg_co),
 .cfg_a(cfg_a),
 .cfg_b(cfg_b),
 .cfg_output(cfg_output),
 .cmd_ready(a_cmd_ready),
 .awaddr(a_awaddr),
 .awlen(a_awlen),
 .awvalid(a_awvalid),
 .wdata(a_wdata),
 .wlast(a_wlast),
 .wvalid(a_wvalid),
 .bready(a_bready),
 .araddr(a_araddr),
 .arlen(a_arlen),
 .arvalid(a_arvalid),
 .rready(a_rready),
 .done(a_done),
 .failed(a_failed),
 .error_code(a_error_code),
 .read_bytes(a_read_bytes),
 .write_bytes(a_write_bytes),
 .words_completed(a_words_completed),
 .valid_scalars(a_valid_scalars),
 .cmd_valid(cmd_valid&&!active&&!select_fast),
 .aligned_valid(legacy_aligned_valid),
 .aligned_address(legacy_aligned_address),
 .aligned_values(legacy_aligned_values));
 nearest_burst #(.WATCHDOG(WATCHDOG)) b_engine(
 .clk(clk),
 .reset(reset),
 .awready(awready),
 .wready(wready),
 .bresp(bresp),
 .bvalid(bvalid),
 .arready(arready),
 .rdata(rdata),
 .rresp(rresp),
 .rlast(rlast),
 .rvalid(rvalid),
 .cfg_h(cfg_h),
 .cfg_w(cfg_w),
 .cfg_ca(cfg_ca),
 .cfg_cb(cfg_cb),
 .cfg_oh(cfg_oh),
 .cfg_ow(cfg_ow),
 .cfg_co(cfg_co),
 .cfg_a(cfg_a),
 .cfg_output(cfg_output),
 .cmd_ready(b_cmd_ready),
 .awaddr(b_awaddr),
 .awlen(b_awlen),
 .awvalid(b_awvalid),
 .wdata(b_wdata),
 .wlast(b_wlast),
 .wvalid(b_wvalid),
 .bready(b_bready),
 .araddr(b_araddr),
 .arlen(b_arlen),
 .arvalid(b_arvalid),
 .rready(b_rready),
 .done(b_done),
 .failed(b_failed),
 .error_code(b_error_code),
 .read_bytes(b_read_bytes),
 .write_bytes(b_write_bytes),
 .words_completed(b_words_completed),
 .valid_scalars(b_valid_scalars),
 .cmd_valid(cmd_valid&&!active&&select_fast));
endmodule
