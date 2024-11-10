import sys
from PyQt5.QtWidgets import QApplication
from gui_components import MainWindow
from plc_control import  initialize_isaac_sim,set_operation_mode,set_target_velocity,load_context,update_isaac_sim,stop_continuous_motion
from plc_control import  MODE_VELOCITY,exit_event,plc,conversation,simulation_app
import threading
from gui_components import run_gui


def main():
    global simulation_app, world, conversation
    initialize_isaac_sim()

    set_operation_mode(MODE_VELOCITY)
    set_target_velocity(0)
    
    data = load_context()
    conversation = data.get("conversation", [])

    print("eRob LLM Controller with Isaac Sim (type 'exit' to quit, 'status' for motor status, 'help' for information)")

    # 启动 GUI 线程
    gui_thread = threading.Thread(target=run_gui, daemon=True)
    gui_thread.start()


    try:
        # 运行Isaac Sim主循环
        update_isaac_sim()
    finally:
        exit_event.set()  # 确保所有线程都收到退出信号
        gui_thread.join(timeout=2)
        stop_continuous_motion()
        set_target_velocity(0)
        plc.close()
        print("PLC connection closed")
        simulation_app.close()

if __name__ == '__main__':
    main()
    #app = QApplication(sys.argv)
    #main_window = MainWindow()  # Create the main window
    #main_window.show()  # Show the window
    #sys.exit(app.exec_())  # Start the application event loop