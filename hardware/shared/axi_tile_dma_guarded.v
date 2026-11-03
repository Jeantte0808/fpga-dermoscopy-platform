`timescale 1ns/1ps
// 128-bit AXI row DMA, one outstanding burst, packed synchronous BRAM port.
// Descriptor addresses/strides/row lengths are in BYTES and 16-byte aligned.
// No partial beats: byte alignment/halo packing belongs in the tensor adapter.
module axi_tile_dma_guarded #(parameter WORD_ADDR_BITS=14,parameter WATCHDOG=100000000)(
 input clk,input reset,
 input cmd_valid,output cmd_ready,input cmd_write,
 input [29:0] cmd_base,input [29:0] cmd_stride,
 input [15:0] cmd_row_bytes,input [15:0] cmd_rows,
 output busy,output reg done,output reg failed,output reg [7:0] error_code,
 output reg [31:0] transferred_bytes,output reg [31:0] cycles,
 output reg [29:0] failure_address,
 output [29:0] awaddr,output [7:0] awlen,output awvalid,input awready,
 output [127:0] wdata,output wlast,output wvalid,input wready,
 input [1:0] bresp,input bvalid,output bready,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,input rvalid,output rready,
 output mem_we,output [WORD_ADDR_BITS-1:0] mem_addr,
 output [127:0] mem_wdata,input [127:0] mem_rdata
);
 localparam IDLE=0,CALC=1,AW=2,FETCH=3,FETCH_WAIT=4,WD=5,WB=6,AR=7,RD=8,ADVANCE=9;
 reg [3:0] state;
 reg writing;
 reg quarantined,aw_pending,ar_pending,w_pending;
 reg [29:0] row_base,address,stride;
 reg [15:0] row_bytes,rows_left,remaining;
 reg [WORD_ADDR_BITS:0] mem_index;
 reg [8:0] burst_beats,beat;
 reg [127:0] write_hold;
 reg [31:0] idle_cycles;
 wire [31:0] descriptor_bytes=cmd_row_bytes*cmd_rows;
 wire [12:0] boundary_bytes=13'd4096-{1'b0,address[11:0]};
 wire [11:0] remaining_beats=remaining[15:4];
 wire [8:0] boundary_beats=boundary_bytes[12:4];
 wire [8:0] row_limit=remaining_beats>256 ? 9'd256:remaining_beats[8:0];
 wire [8:0] next_beats=row_limit<boundary_beats ? row_limit:boundary_beats;
 wire [12:0] burst_bytes={burst_beats,4'b0};
 wire [30:0] following_row={1'b0,row_base}+{1'b0,stride};
 assign cmd_ready=state==IDLE&&!quarantined;
 assign busy=state!=IDLE||quarantined;
 assign awaddr=address;assign araddr=address;
 assign awlen=burst_beats-1'b1;assign arlen=burst_beats-1'b1;
 assign awvalid=quarantined ? aw_pending:state==AW;assign arvalid=quarantined ? ar_pending:state==AR;
 assign wvalid=quarantined ? w_pending:state==WD;assign wdata=write_hold;
 assign wlast=beat==burst_beats-1'b1;
 assign bready=quarantined||state==WB;assign rready=quarantined||state==RD;
 assign mem_we=!quarantined && state==RD && rvalid && rresp==0 && rlast==(beat==burst_beats-1'b1);
 assign mem_addr=mem_index[WORD_ADDR_BITS-1:0];assign mem_wdata=rdata;
 wire activity=(awvalid&&awready)||(wvalid&&wready)||(bready&&bvalid)||(arvalid&&arready)||(rready&&rvalid);
 task stop_error;input [7:0] code;
 begin failed<=1;done<=1;error_code<=code;failure_address<=address+{beat,4'b0};state<=IDLE;quarantined<=1;aw_pending<=state==AW&&!awready;ar_pending<=state==AR&&!arready;w_pending<=state==WD&&!wready;end
 endtask
 always @(posedge clk) begin
 if(reset) begin
 state<=IDLE;quarantined<=0;aw_pending<=0;ar_pending<=0;w_pending<=0;writing<=0;row_base<=0;address<=0;stride<=0;row_bytes<=0;
 rows_left<=0;remaining<=0;mem_index<=0;burst_beats<=0;beat<=0;write_hold<=0;
 done<=0;failed<=0;error_code<=0;failure_address<=0;transferred_bytes<=0;cycles<=0;idle_cycles<=0;
 end else if(quarantined) begin done<=0;
 if(awvalid&&awready)aw_pending<=0;
 if(arvalid&&arready)ar_pending<=0;
 if(wvalid&&wready)w_pending<=0;
 end else begin
 done<=0;
 if(busy) begin
 cycles<=cycles+1;
 if(activity || state==CALC || state==FETCH || state==FETCH_WAIT || state==ADVANCE) idle_cycles<=0;
 else idle_cycles<=idle_cycles+1;
 end else idle_cycles<=0;
 case(state)
 IDLE:if(cmd_valid) begin
 done<=0;failed<=0;error_code<=0;failure_address<=0;transferred_bytes<=0;cycles<=0;
 if(cmd_base[3:0]!=0 || cmd_stride[3:0]!=0 || cmd_row_bytes[3:0]!=0 ||
 cmd_row_bytes==0 || cmd_rows==0 || descriptor_bytes>(16*(2**WORD_ADDR_BITS)) ||
 (cmd_rows>1 && cmd_stride<cmd_row_bytes) || ({1'b0,cmd_base}+cmd_row_bytes)>31'h40000000) begin
 failed<=1;done<=1;error_code<=6;failure_address<=cmd_base;
 end else begin
 writing<=cmd_write;row_base<=cmd_base;address<=cmd_base;stride<=cmd_stride;
 row_bytes<=cmd_row_bytes;rows_left<=cmd_rows;remaining<=cmd_row_bytes;
 mem_index<=0;state<=CALC;
 end end
 CALC:begin burst_beats<=next_beats;beat<=0;state<=writing ? AW:AR;end
 AW:if(awready) state<=FETCH;
 FETCH:state<=FETCH_WAIT;
 FETCH_WAIT:begin write_hold<=mem_rdata;state<=WD;end
 WD:if(wready) begin
 mem_index<=mem_index+1;transferred_bytes<=transferred_bytes+16;
 if(wlast) state<=WB;else begin beat<=beat+1;state<=FETCH;end
 end
 WB:if(bvalid) begin if(bresp!=0) stop_error(2);else state<=ADVANCE;end
 AR:if(arready) state<=RD;
 RD:if(rvalid) begin
 if(rresp!=0) stop_error(3);
 else if(rlast!=(beat==burst_beats-1'b1)) stop_error(4);
 else begin
 mem_index<=mem_index+1;transferred_bytes<=transferred_bytes+16;
 if(rlast) state<=ADVANCE;else beat<=beat+1;
 end end
 ADVANCE:begin
 if(remaining>burst_bytes) begin
 address<=address+burst_bytes;remaining<=remaining-burst_bytes;state<=CALC;
 end else if(rows_left>1) begin
 if(following_row[30] || following_row+row_bytes>31'h40000000) stop_error(6);
 else begin
 row_base<=following_row[29:0];address<=following_row[29:0];
 remaining<=row_bytes;rows_left<=rows_left-1;state<=CALC;
 end end
 else begin done<=1;state<=IDLE;end
 end
 default:state<=IDLE;
 endcase
 if(busy && idle_cycles>=WATCHDOG) stop_error(5);
 end end
endmodule
