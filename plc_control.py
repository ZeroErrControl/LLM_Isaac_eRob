import pyads
import time
import subprocess
import re
import json
import os
import sys
import threading
import queue
from omni.isaac.kit import SimulationApp
import traceback
import tempfile



# 创建一个锁
lock = threading.Lock()
# 全局变量
exit_event = threading.Event()
isaac_sim_thread_running = threading.Event()
continuous_motion = False
motion_thread = None
simulation_app = None
world = None
sim_motor_prim = None

current_velocity = 0
actual_position = 0

user_input_queue = queue.Queue()
motion_stop_event = threading.Event()
# 全局变量
conversation = []  # 确保在文件顶部定义
llm_response = None  # 初始化为 None

PULSES_PER_REVOLUTION = 524288
CONTEXT_FILE = "llm_context.json"
GOOD_PROMPT_FILE = "good_prompt.json"
MODE_VELOCITY = 3  # CSV mode

# 连接到 TwinCAT PLC
plc = pyads.Connection('192.168.1.160.1.1', 851)
plc.open()


# 全局变量用于状态更新
last_status_update = 0
status_update_interval = 0.1  # 状态更新间隔（秒）

def update_status_display(position, velocity, elapsed_time, progress=None, message=""):
    global last_status_update
    current_time = time.time()
    if current_time - last_status_update >= status_update_interval:
        if progress is not None:
            progress_bar = ">" * int(progress * 20) + " " * (20 - int(progress * 20))
            status = f"\r{message} | Pos: {position:8d} | Vel: {velocity:8d} | Time: {elapsed_time:6.1f}s [{progress_bar}]"
        else:
            status = f"\r{message} | Pos: {position:8d} | Vel: {velocity:8d} | Time: {elapsed_time:6.1f}s"
        sys.stdout.write(status)
        sys.stdout.flush()
        last_status_update = current_time


simulation_app = SimulationApp({"headless": False})
def initialize_isaac_sim():
    global simulation_app, world, sim_motor_prim
    print("Initializing Isaac Sim...")

    print("SimulationApp created")

    from omni.isaac.core.utils.stage import open_stage
    from omni.isaac.core import World
    from omni.isaac.core.utils.prims import get_prim_at_path

    file_path = "D:\Isaac_sim\eRob_LLM_TC_MIN.usd"
    print(f"Opening stage: {file_path}")
    open_stage(usd_path=file_path)
    print("Stage opened")
    
    world = World()
    print("World created")
    world.reset()
    print("World reset")

    sim_motor_prim = get_prim_at_path("/World/eROb110_ok/erob110h_i_SWG2/node_/mesh_/RevoluteJoint")
    if sim_motor_prim.IsValid():
        print("Valid prim found in Isaac Sim")
    else:
        print("Invalid prim in Isaac Sim")
        simulation_app.close()
        sys.exit(1)

def set_sim_motor_velocity(velocity_pulses):
    global sim_motor_prim

    if sim_motor_prim:
        velocity_degrees = round((velocity_pulses / PULSES_PER_REVOLUTION) * 360, 8)
        #print(f"Setting Isaac Sim velocity to {velocity_degrees} deg/s")
        sim_motor_prim.GetAttribute("drive:angular:physics:targetVelocity").Set(float(velocity_degrees))
        actual_speed = sim_motor_prim.GetAttribute("drive:angular:physics:targetVelocity").Get()
        #time.sleep(0.01)  # 添加小延迟，减少通信频率
        #print(f"Isaac Sim Motor Speed set to: {actual_speed} deg/s ({velocity_pulses} pulses/s)")
    else:
        print("Error: sim_motor_prim is not valid")

def update_isaac_sim():
    print("Isaac Sim main loop started")
    try:
        while not exit_event.is_set() and simulation_app.is_running():
            if sim_motor_prim:
                velocity_degrees = (current_velocity / PULSES_PER_REVOLUTION) * 360
                sim_motor_prim.GetAttribute("drive:angular:physics:targetVelocity").Set(float(velocity_degrees))
            world.step(render=True)
            #time.sleep(0.01)  # 添加小延迟，减少通信频率
            process_user_input()
    except Exception as e:
        print(f"\nError in Isaac Sim main loop: {e}")
    finally:
        print("\nIsaac Sim main loop stopped")

def process_user_input():
    global conversation
    try:
        user_input = user_input_queue.get_nowait()
        if user_input.lower() == 'exit':
            exit_event.set()
        elif user_input.lower() == 'status':
            print_motor_status()
        elif user_input.lower() == 'help':
            print_help()
        elif user_input.lower() in ['good', 'well']:
            save_good_prompt(conversation)
        else:
            conversation.append({"role": "Human", "content": user_input})
            llm_response = interpret_and_execute_command(user_input, conversation)
            if llm_response:
                conversation.append({"role": "Assistant", "content": llm_response})
            save_context({"conversation": conversation})
    except queue.Empty:
        pass


def move_with_velocity(velocity, duration):
    global current_velocity
    start_time = time.time()
    current_velocity = velocity
    set_target_velocity(velocity)
    print(f"Moving with velocity: {velocity} for {duration} seconds")
    set_sim_motor_velocity(velocity)
    
    try:
        while (time.time() - start_time < duration) and not continuous_motion and not exit_event.is_set():
            actual_position, actual_velocity, _ = read_plc_variables()
            time.sleep(0.002)  # 添加小延迟，减少通信频率
            if actual_position is not None:
                elapsed_time = time.time() - start_time
                progress = int((elapsed_time / duration) * 10)
                progress_bar = ">" * progress + " " * (10 - progress)
                sys.stdout.write(f"\rCurrent position: {actual_position}, Current velocity: {actual_velocity}, Duration: {elapsed_time:.1f}s [{progress_bar}]")
                sys.stdout.flush()
            world.step(render=True)
        
        # 开始减速过程
        deceleration_time = 2  # 减速时间，可以根据需要调整
        deceleration_start = time.time()
        while time.time() - deceleration_start < deceleration_time and not exit_event.is_set():
            progress = (time.time() - deceleration_start) / deceleration_time
            current_velocity = int(velocity * (1 - progress))
            set_target_velocity(current_velocity)
            set_sim_motor_velocity(current_velocity)
            world.step(render=True)
            time.sleep(0.002)  # 添加小延迟，减少通信频率
        current_velocity = 0
        set_target_velocity(0)
        set_sim_motor_velocity(0)
        print("\nMotion completed\n")
        print("user:")
    except Exception as e:
        print(f"\nError during motion: {e}")
    finally:
        # 确保在任何情况下都停止运动
        current_velocity = 0
        set_target_velocity(0)
        set_sim_motor_velocity(0)

def angle_to_pulses(angle):
    PULSES_PER_REVOLUTION = 524288
    pulses = int((angle / 360) * PULSES_PER_REVOLUTION)
    return pulses


def continuous_motion_thread(velocity):
    global continuous_motion, current_velocity
    with lock:  # 使用锁保护共享资源
        continuous_motion = True
        motion_stop_event.clear()
        current_velocity = velocity
        set_target_velocity(velocity)
        
        print(f"Starting continuous motion with velocity: {velocity}")
        start_time = time.time()
        set_sim_motor_velocity(velocity)

        try:
            while not exit_event.is_set() and not motion_stop_event.is_set():
                actual_position, actual_velocity, _ = read_plc_variables()
                if actual_position is None:
                    print("\nError: Failed to read PLC variables. Stopping motion.")
                    break
                elapsed_time = time.time() - start_time
                sys.stdout.write(f"\rContinuous motion - Position: {actual_position}, Velocity: {actual_velocity}, Time: {elapsed_time:.1f}s")
                sys.stdout.flush()
                world.step(render=True)

        except Exception as e:
            print(f"\nError in continuous motion: {e}")
        finally:
            current_velocity = 0
            set_target_velocity(0)
            set_sim_motor_velocity(0)
            sys.stdout.write("\r" + " " * 80 + "\r")
            sys.stdout.flush()
            print("Continuous motion stopped\n")
            print("user:")
            continuous_motion = False

def stop_continuous_motion():
    global continuous_motion, motion_thread
    if continuous_motion:
        motion_stop_event.set()
        if motion_thread:
            motion_thread.join(timeout=2)
        continuous_motion = False
        motion_thread = None
        set_target_velocity(0)
        sys.stdout.write("\r" + " " * 80 + "\r")
        sys.stdout.flush()
        print("Continuous motion has been stopped.\n")

def user_input_thread():
    with lock:  # 使用锁保护共享资源
        while not exit_event.is_set():
            user_input = input("\n\nUser:")
            user_input_queue.put(user_input)
            if user_input.lower() == 'exit':
                exit_event.set()
                break

def load_context():
    if os.path.exists(CONTEXT_FILE):
        with open(CONTEXT_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                print("Warning: Context file is not properly formatted. Using empty context.")
    return {"conversation": []}

def save_context(data):
    with open(CONTEXT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def save_good_prompt(conversation):
    with open(GOOD_PROMPT_FILE, 'w', encoding='utf-8') as f:
        json.dump({"conversation": conversation}, f, ensure_ascii=False, indent=2)
    print("Current conversation saved as a good prompt template.")

def get_model_output(prompt, conversation):
    print("Interpreting command...")
    context = """You are an AI assistant capable of chatting conversationally and controlling eRob based on commands.
        For regular conversation, respond naturally.
        For robot control commands, interpret and respond with specific parameter values in the exact format:
        velocity=X
        duration=Y
        position=Z
        continuous=True/False
        angle=A
        Only include parameters that are explicitly mentioned or directly inferred from the command.
        Do not add any explanation or additional text.
        For rotation commands, use the 'angle' parameter.
        Example: 'rotate to 30 degrees' should be interpreted as 'angle=30'."""
    

    # 只使用最近的三次对话
    recent_conversation = conversation[-3:]
    for entry in recent_conversation:
        context += f"{entry['role']}: {entry['content']}\n"
    
    full_prompt = f"{context}\nHuman: {prompt}\nAssistant:"
    try:
        process = subprocess.Popen(
            ['ollama', 'run', 'llama3.1:latest', full_prompt],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='ignore'
        )
        stdout, stderr = process.communicate()
        clean_output = re.sub(r'failed to get console mode.*\n?', '', stdout).strip()
        return clean_output
    except Exception as e:
        print(f"Error running Ollama: {e}")
        return None

def set_operation_mode(mode):
    try:
        plc.write_by_name("GVL.Operation_Mode", mode, pyads.PLCTYPE_SINT)
        print(f"Operation mode set to: {mode}")
    except pyads.ADSError as e:
        print(f"ADS Error when setting operation mode: {e}")




def set_target_velocity(velocity):
    global current_velocity
    try:
                # 读取当前实际速度
        actual_velocity = plc.read_by_name("GVL.Actual_Velocity", pyads.PLCTYPE_DINT)
        
        plc.write_by_name("GVL.Target_Velocity", velocity, pyads.PLCTYPE_DINT)
        plc.write_by_name("GVL.Profile_acceleration",max(abs(actual_velocity),abs(velocity)), pyads.PLCTYPE_DINT)
        plc.write_by_name("GVL.Profile_deceleration",max(abs(actual_velocity),abs(velocity)), pyads.PLCTYPE_DINT)
        current_velocity = velocity
        time.sleep(0.002)  # 添加小延迟，减少通信频率
        #sys.stdout.write("\r" + "eRob Speed set to:  {current_velocity} pulses/s) " * 80 + "\r")  # Clear the current line
        #sys.stdout.flush()
    except pyads.ADSError as e:
        print(f"ADS Error when setting target velocity: {e}")

def read_plc_variables():
    try:
        actual_position = plc.read_by_name("GVL.Actual_Position", pyads.PLCTYPE_DINT)
        actual_velocity = plc.read_by_name("GVL.Actual_Velocity", pyads.PLCTYPE_DINT)
        target_velocity = plc.read_by_name("GVL.Target_Velocity", pyads.PLCTYPE_DINT)
        time.sleep(0.002)  # 添加小延迟，减少通信频率
        return actual_position, actual_velocity, target_velocity
    except pyads.ADSError as e:
        print(f"ADS Error when reading PLC variables: {e}")
        return None, None, None

def move_to_position_csv(target_position):
    global actual_position
    print(f"Moving to position: {target_position}")
    set_operation_mode(MODE_VELOCITY)
    start_time = time.time()
    actual_position, _, _ = read_plc_variables()
    if actual_position is None:
        print("Failed to read actual position")
        return

    initial_distance = abs(target_position - actual_position)

    try:
        while abs(actual_position - target_position) > 5 and not exit_event.is_set():
            distance = target_position - actual_position
            velocity = min(60000, max(100, abs(distance)))
            if distance < 0:
                velocity = -velocity

            set_target_velocity(velocity)
            set_sim_motor_velocity(velocity)
            time.sleep(0.002)  # 添加小延迟，减少通信频率
            elapsed_time = time.time() - start_time
            progress = 1 - (abs(distance) / initial_distance)
            update_status_display(actual_position, velocity, elapsed_time, progress, "Moving")
            
            world.step(render=True)
            actual_position, _, _ = read_plc_variables()
            if actual_position is None:
                print("\nFailed to read actual position")
                return

    except Exception as e:
        print(f"\nError during position movement: {e}")
    finally:
        set_target_velocity(0)
        set_sim_motor_velocity(0)
        final_time = time.time() - start_time
        update_status_display(actual_position, 0, final_time, 1.0, "Reached") # 
        print("\nUser:")

def print_motor_status():
    actual_position, actual_velocity, target_velocity = read_plc_variables()
    if actual_position is not None:
        print(f"\nMotor Status: Position: {actual_position}, Velocity: {actual_velocity}, Target Velocity: {target_velocity}")
    else:
        print("Failed to read motor status")

def interpret_and_execute_command(command, conversation):
    global continuous_motion, motion_thread, llm_response
    
    if check_stop_condition():
        print("Stop condition detected. Aborting command execution.")
        return None

    llm_response = get_model_output(command, conversation)
    if llm_response:
        print(f"LLM interpretation: {llm_response}")
        llm_response_lower = llm_response.lower()
        
        mode_switch = 0b00000000  # 8 位二进制，初始值为 0
        mode_switch |= (1<<5) if re.search(r'position\s*[=:]\s*(-?\d+)', llm_response_lower) else 0 
        mode_switch |= (1<<4) if re.search(r'velocity\s*[=:]\s*(-?\d+)', llm_response_lower) else 0  
        mode_switch |= (1<<3) if re.search(r'duration\s*[=:]\s*(\d+(?:\.\d+)?)', llm_response_lower) else 0 
        mode_switch |= (1<<2) if re.search(r'continuous\s*[=:]\s*(true|false)', llm_response_lower) else 0
        mode_switch |= (1<<1) if re.search(r'angle\s*=\s*(-?\d+(?:\.\d+)?)', llm_response_lower) else 0 
        mode_switch |= (1<<0) if re.search(r'stop|halt|pause', command, re.IGNORECASE) else 0 
        print("mode_switch ", bin(mode_switch)[2:])

        position_match = re.search(r'position\s*[=:]\s*(-?\d+)', llm_response_lower)
        velocity_match = re.search(r'velocity\s*[=:]\s*(-?\d+)', llm_response_lower)
        duration_match = re.search(r'duration\s*[=:]\s*(\d+(?:\.\d+)?)', llm_response_lower)
        continuous_match = re.search(r'continuous\s*[=:]\s*(true|false)', llm_response_lower)
        angle_match = re.search(r'angle\s*=\s*(-?\d+(?:\.\d+)?)', llm_response_lower)
        stop_match = re.search(r'stop|halt|pause|velocity\s*=None|0', command, re.IGNORECASE)

        # 根据 mode_switch 的值判定运行工况
        if mode_switch & (1 << 0) or (mode_switch & (0 << 2) and mode_switch & (0 << 4)) :
            stop_continuous_motion()
            print("Stop condition detected. Aborting command execution.")
        elif mode_switch & (1 << 5):  # position
            position = int(position_match.group(1))
            print(f"Moving to position: {position}")
            return move_to_position_csv(position)
        elif mode_switch & (1 << 4) or (mode_switch & (1 << 4) and ((mode_switch & (1 << 2)) or (mode_switch & (1 << 3)))): # position
            velocity = int(velocity_match.group(1))
            return execute_velocity_command(velocity, duration_match, continuous_match)
        elif mode_switch & (1 << 1):
            angle = float(angle_match.group(1))
            return execute_angle_rotation(angle)
        else:
            print("Could not extract valid parameters from LLM response. Using original command.")
            return parse_original_command(command)
        
    else:
        print("Failed to interpret command.")
        return parse_original_command(command)

def execute_angle_rotation(angle):
    pulses = angle_to_pulses(angle)
    current_position, _, _ = read_plc_variables()
    target_position = current_position + pulses
    print(f"Rotating {angle} degrees (equivalent to {pulses} pulses)")
    return move_to_position_csv(target_position)

def execute_velocity_command(velocity, duration_match, continuous_match):
    if duration_match:
        duration = float(duration_match.group(1))
        return move_with_velocity(velocity, duration)
    elif continuous_match and continuous_match.group(1) == 'true':
        motion_thread = threading.Thread(target=continuous_motion_thread, args=(velocity,))
        motion_thread.start()
        set_sim_motor_velocity(velocity)
        print(f"Started continuous motion with velocity: {velocity}")
    else:
        print(f"Setting velocity to {velocity} without specified duration\n")
        print(f"User:")
        set_target_velocity(velocity)
        set_sim_motor_velocity(velocity)
    return None

def parse_original_command(command):
    position_match = re.search(r'position\s*=?\s*(-?\d+)&continue\s*=?\s*false', command)
    velocity_match = re.search(r'velocity\s*=?\s*(-?\d+)', command)
    duration_match = re.search(r'for\s+(\d+(?:\.\d+)?)\s*s & continue\s*=?\s*false', command)
    continuous_match = re.search(r'continue\s*=?\s*true', command, re.IGNORECASE)
    angle_match = re.search(r'rotate\s*(-?\d+(?:\.\d+)?)\s*degrees?', command, re.IGNORECASE)
    stop_match = re.search(r'stop|halt|pause|velocity\s*=None|0', command, re.IGNORECASE)


    if check_stop_condition():
        print("Stop condition detected. Aborting command execution.")
        return None

    if stop_match:
        stop_continuous_motion()
        print("Continuous motion has been stopped.")
    elif angle_match:
        angle = float(angle_match.group(1))
        return execute_angle_rotation(angle)
    elif position_match:
        position = int(position_match.group(1))
        print(f"Moving to position: {position}")
        return move_to_position_csv(position)
    elif velocity_match:
        velocity = int(velocity_match.group(1))
        return execute_velocity_command(velocity, duration_match, continuous_match)
    else:
        print("Could not extract valid parameters from original command.")
    return None

def check_stop_condition():
    return exit_event.is_set() or motion_stop_event.is_set()

def print_help():
    print("Available commands:")
    print("'help' - Show this help message")
    print("'status' - Show current motor status")
    print("'good' or 'well' - Save the current conversation as a good prompt")
    print("'exit' - Exit the program")
    print("++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++")
    print("\nYou can control the eRob using natural language commands.")
    print("++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++")
    print("Examples:")
    print("- 'Move the robot at 1000 units per second for 5 seconds'")
    print("- 'Set the velocity to -5000 for 30 seconds'")
    print("- 'Move to position 10000'")
    print("- 'Start continuous motion at velocity 2000'")
    print("- 'Stop continuous motion'")
    print("Any other input - Continue the conversation and control the robot")

#from gui_components import run_gui



