`timescale 1ns/1ps
// Passive, single-outstanding AXI instrumentation. No control back into the core.
// Counts edges after accepted graph start, through graph_done/fault inclusive.
module formal_perf_counters(
 input clk,reset,start,stop,fault,input [31:0] frame,generation,input [6:0] stage,
 input arvalid,arready,input [7:0] arlen,input rvalid,rready,rlast,
 input awvalid,awready,input [7:0] awlen,input wvalid,wready,wlast,input bvalid,bready,
 input [6:0] query_record,output reg query_valid,output reg [511:0] query_data,
 output reg [31:0] query_frame,query_generation,output reg active,complete,aborted,saturated
);
 reg [31:0] total[0:14],current[0:14];
 (* ram_style="block" *) reg [511:0] records[0:81];
 reg [511:0] ram_q,summary_q;reg summary_select,valid_q;reg [31:0] frame_q,generation_q;
 reg [81:0] written;reg [6:0] current_stage;reg finish_pending,read_pending,write_pending,last_w;
 reg [31:0] saved_frame,saved_generation;
 wire [14:0] increment={
 !read_pending&&!write_pending&&!arvalid&&!awvalid&&!rvalid&&!wvalid&&!bvalid,
 awvalid&&awready&&awlen<=1,arvalid&&arready&&arlen<=1,
 rvalid&&!rready,wvalid&&!wready,awvalid&&!awready,arvalid&&!arready,
 write_pending&&last_w&&!bvalid,write_pending&&!last_w&&wready&&!wvalid,
 read_pending&&!rvalid,wvalid&&wready,awvalid&&awready,rvalid&&rready,arvalid&&arready,1'b1};
 reg [511:0] packed_current,packed_total;integer i;
 always @* begin
  packed_current=0;packed_total=0;
  for(integer k=0;k<15;k=k+1)begin
   packed_current[k*32+:32]=current[k];packed_total[k*32+:32]=total[k];
  end
  packed_current[480+:32]={29'd0,saturated,aborted,complete};
  packed_total[480+:32]={29'd0,saturated,aborted,complete};
 end
 always @(posedge clk)begin
  ram_q<=records[(query_record>=1&&query_record<=82)?query_record-1:0];
  summary_q<=packed_total;summary_select<=query_record==0;
  valid_q<=complete&&!active&&!finish_pending&&(query_record==0||(query_record<=82&&written[query_record-1]));
  frame_q<=saved_frame;generation_q<=saved_generation;
  query_data<=summary_select?summary_q:ram_q;query_valid<=valid_q;
  query_frame<=frame_q;query_generation<=generation_q;
  if(reset)begin
   active<=0;complete<=0;aborted<=0;saturated<=0;finish_pending<=0;written<=0;
   current_stage<=0;read_pending<=0;write_pending<=0;last_w<=0;saved_frame<=0;saved_generation<=0;
   query_valid<=0;valid_q<=0;query_frame<=0;query_generation<=0;frame_q<=0;generation_q<=0;
   for(i=0;i<15;i=i+1)begin total[i]<=0;current[i]<=0;end
  end else if(start)begin
   active<=1;complete<=0;aborted<=0;saturated<=0;finish_pending<=0;written<=0;
   current_stage<=0;read_pending<=0;write_pending<=0;last_w<=0;saved_frame<=frame;saved_generation<=generation;
   query_valid<=0;valid_q<=0;
   for(i=0;i<15;i=i+1)begin total[i]<=0;current[i]<=0;end
  end else if(active)begin
   if(arvalid&&arready)read_pending<=1;
   if(rvalid&&rready&&rlast)read_pending<=0;
   if(awvalid&&awready)begin write_pending<=1;last_w<=0;end
   if(wvalid&&wready&&wlast)last_w<=1;
   if(bvalid&&bready)begin write_pending<=0;last_w<=0;end
   for(i=0;i<15;i=i+1)if(increment[i])begin
    if(total[i]!=32'hffffffff)total[i]<=total[i]+1'b1;else saturated<=1;
   end
   if(stage!=current_stage&&stage<82)begin
    records[current_stage]<=packed_current;written[current_stage]<=1;current_stage<=stage;
    for(i=0;i<15;i=i+1)current[i]<=increment[i]?32'd1:32'd0;
   end else for(i=0;i<15;i=i+1)if(increment[i])begin
    if(current[i]!=32'hffffffff)current[i]<=current[i]+1'b1;else saturated<=1;
   end
   if(stop||fault)begin active<=0;aborted<=fault;finish_pending<=1;end
  end else if(finish_pending)begin
   records[current_stage]<=packed_current;written[current_stage]<=1;complete<=1;finish_pending<=0;
  end
 end
endmodule
