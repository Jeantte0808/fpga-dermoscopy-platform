`timescale 1ns/1ps
// Fixed, SHA-bound descriptor ROM. model_verified is a trusted integration input:
// the future hardware CRC/session manager must drive it, never a wire START alone.
module formal_graph_control #(parameter WATCHDOG=100000)(
 input clk,reset,cmd_valid,output cmd_ready,
 input model_verified,input [31:0] model_generation,input [31:0] frame_id,
 output reg execute_valid,input execute_ready,input execute_done,execute_failed,
 input [7:0] execute_error,input [31:0] execute_write_bytes,execute_scalars,
 input read_pending,write_pending,
 output reg [1023:0] descriptor,output reg [2:0] owner,
 output reg [6:0] stage_index,output reg [31:0] completed_stages,
 output reg [31:0] active_frame_id,active_generation,
 output reg busy,done,failed,result_valid,output reg [7:0] error_code
);
 localparam IDLE=0,FETCH=1,LATCH=2,ISSUE=3,RUN=4,DRAIN=5,ADVANCE=6,FAULT=7;
 reg [2:0] state;reg [31:0] wait_cycles;
 (* rom_style="block" *) reg [1023:0] rom[0:81];reg [1023:0] rom_q;
 initial $readmemh("generated/descriptors.mem",rom);
 always @(posedge clk)rom_q<=rom[stage_index];
 assign cmd_ready=state==IDLE&&!failed&&!read_pending&&!write_pending;
 wire [31:0] op=rom_q[31:0];
 wire [31:0] expected_bytes=descriptor[28*32+:32],expected_scalars=descriptor[29*32+:32];
 task fault;input [7:0] code;begin execute_valid<=execute_valid&&!execute_ready;busy<=0;done<=1;failed<=1;result_valid<=0;error_code<=code;state<=FAULT;end endtask
 always @(posedge clk)begin
 if(reset)begin state<=IDLE;wait_cycles<=0;execute_valid<=0;descriptor<=0;owner<=0;stage_index<=0;completed_stages<=0;active_frame_id<=0;active_generation<=0;busy<=0;done<=0;failed<=0;result_valid<=0;error_code<=0;end
 else begin
 done<=0;
 if(state==ISSUE||state==DRAIN)wait_cycles<=wait_cycles+1;else wait_cycles<=0;
 case(state)
 IDLE:if(cmd_valid&&cmd_ready)begin
 result_valid<=0;completed_stages<=0;stage_index<=0;active_frame_id<=frame_id;active_generation<=model_generation;error_code<=0;
 if(!model_verified)fault(16);else begin busy<=1;state<=FETCH;end end
 FETCH:state<=LATCH;
 LATCH:begin
 descriptor<=rom_q;
 case(op)0,1:owner<=1;2:owner<=2;3,4:owner<=3;5:owner<=0;6:owner<=4;default:fault(17);endcase
 if(op<=6)begin execute_valid<=1;state<=ISSUE;end end
 ISSUE:if(execute_ready)begin execute_valid<=0;state<=RUN;end
 RUN:if(execute_done)begin
 if(execute_failed)fault(execute_error);
 else if(execute_write_bytes!=expected_bytes||execute_scalars!=expected_scalars)fault(18);
 else state<=DRAIN;end
 DRAIN:if(!read_pending&&!write_pending)state<=ADVANCE;
 ADVANCE:begin
 completed_stages<=completed_stages+1;
 if(stage_index==81)begin busy<=0;done<=1;result_valid<=1;state<=IDLE;end
 else begin stage_index<=stage_index+1'b1;state<=FETCH;end end
 FAULT:if(execute_valid&&execute_ready)execute_valid<=0;
 default:fault(17);
 endcase
 if(state!=IDLE&&state!=FAULT&&(!model_verified||model_generation!=active_generation))fault(19);
 else if((state==ISSUE||state==DRAIN)&&wait_cycles>=WATCHDOG)fault(5);
 end end
endmodule
