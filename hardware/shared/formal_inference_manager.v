`timescale 1ns/1ps
// Transport-independent ownership: begin update -> external DDR upload -> real
// CRC preflight -> inference -> retained result -> consumer acknowledgement.
// All failures retain the AXI lease until a coordinated reset.
module formal_inference_manager #(parameter WATCHDOG=100000)(
 input clk,reset,configuration_busy,
 input update_valid,output update_ready,output model_write_allowed,input model_write_event,
 input verify_valid,output verify_ready,input [255:0] verify_image_id,
 input frame_valid,output frame_ready,input [31:0] frame_id,input result_ready,
 output reg verify_done,frame_done,output result_valid,model_verified,
 output busy,axi_owned,output reg failed,output reg [7:0] error_code,
 output reg [31:0] generation,output reg [31:0] result_frame_id,result_generation,
 output reg owner,input read_pending,write_pending,
 output preflight_valid,input preflight_ready,preflight_done,preflight_failed,
 input [7:0] preflight_error,input preflight_verified,input [31:0] preflight_generation,
 output preflight_invalidate,output reg [255:0] requested_image_id,
 output graph_valid,input graph_ready,graph_done,graph_failed,input [7:0] graph_error,
 input graph_result_valid,input [31:0] graph_result_frame,graph_result_generation,
 output reg [31:0] graph_frame_id
);
 localparam IDLE=0,VLAUNCH=1,VRUN=2,GLAUNCH=3,GRUN=4,DRAIN=5,RESULT=6,FAULT=7;
 reg [2:0] state;reg upload_armed,operation_graph,hold_command;reg [31:0] wait_cycles;
 wire idle=state==IDLE&&!failed&&!configuration_busy&&!read_pending&&!write_pending;
 assign update_ready=idle;
 assign verify_ready=idle&&upload_armed&&!update_valid;
 assign model_write_allowed=state==IDLE&&upload_armed&&!failed&&!preflight_verified&&
                           !(verify_valid&&verify_ready)&&!(update_valid&&update_ready);
 wire illegal_write=model_write_event&&!model_write_allowed;
 assign preflight_invalidate=(update_valid&&update_ready)||illegal_write||failed;
 assign model_verified=preflight_verified&&preflight_generation==generation&&!preflight_invalidate&&!failed;
 assign frame_ready=idle&&model_verified&&!update_valid&&!verify_valid;
 assign preflight_valid=state==VLAUNCH||(state==FAULT&&hold_command&&!owner);
 assign graph_valid=state==GLAUNCH||(state==FAULT&&hold_command&&owner);
 assign busy=state!=IDLE&&state!=RESULT&&state!=FAULT;
 assign axi_owned=busy||failed;
 assign result_valid=state==RESULT&&model_verified&&graph_result_valid&&!failed;
 task fault;input [7:0] code;begin
 failed<=1;error_code<=code;
 hold_command<=(state==VLAUNCH&&!preflight_ready)||(state==GLAUNCH&&!graph_ready);
 state<=FAULT;
 end endtask
 always @(posedge clk)begin
 if(reset)begin state<=IDLE;upload_armed<=0;operation_graph<=0;hold_command<=0;wait_cycles<=0;
 verify_done<=0;frame_done<=0;failed<=0;error_code<=0;generation<=0;owner<=0;
 result_frame_id<=0;result_generation<=0;requested_image_id<=0;graph_frame_id<=0;end
 else begin
 verify_done<=0;frame_done<=0;
 if(state==VLAUNCH||state==GLAUNCH||state==DRAIN)wait_cycles<=wait_cycles+1;else wait_cycles<=0;
 case(state)
 IDLE:begin
 if(update_valid&&update_ready)begin
 generation<=generation==32'hffffffff ? 32'd1 : generation+1'b1;upload_armed<=1;
 end else if(verify_valid&&verify_ready)begin
 requested_image_id<=verify_image_id;owner<=0;operation_graph<=0;upload_armed<=0;state<=VLAUNCH;
 end else if(frame_valid&&frame_ready)begin
 graph_frame_id<=frame_id;result_frame_id<=frame_id;result_generation<=generation;
 owner<=1;operation_graph<=1;state<=GLAUNCH;
 end end
 VLAUNCH:if(preflight_ready)state<=VRUN;
 VRUN:if(preflight_done)begin
 if(preflight_failed)fault(preflight_error);
 else if(!preflight_verified||preflight_generation!=generation)fault(23);
 else state<=DRAIN;end
 GLAUNCH:if(graph_ready)state<=GRUN;
 GRUN:if(graph_done)begin
 if(graph_failed)fault(graph_error);
 else if(!graph_result_valid||graph_result_frame!=result_frame_id||graph_result_generation!=result_generation)fault(24);
 else state<=DRAIN;end
 DRAIN:if(!read_pending&&!write_pending)begin
 if(operation_graph)begin frame_done<=1;state<=RESULT;end
 else begin verify_done<=1;state<=IDLE;end end
 RESULT:begin
 if(!graph_result_valid||graph_result_frame!=result_frame_id||graph_result_generation!=result_generation)fault(24);
 else if(result_ready&&!configuration_busy&&!read_pending&&!write_pending)state<=IDLE;end
 FAULT:if(hold_command&&((owner&&graph_ready)||(!owner&&preflight_ready)))hold_command<=0;
 default:fault(17);
 endcase
 if(state!=FAULT&&illegal_write)fault(22);
 else if((state==GLAUNCH||state==GRUN||state==RESULT||(state==DRAIN&&operation_graph))&&!model_verified)fault(19);
 else if((state==VLAUNCH||state==GLAUNCH||state==DRAIN)&&wait_cycles>=WATCHDOG)fault(5);
 end end
endmodule
