import subprocess  # Used to execute external commands
import re  # For regular expression operations
import json  # For handling JSON data
import os  # For interacting with the operating system
import sys  # For accessing system-specific parameters and functions
import speech_recognition as sr  # Import SpeechRecognition for voice input
from gtts import gTTS  # Import gTTS for text-to-speech
from playsound import playsound  # Import playsound to play audio
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel, QScrollArea, QFrame, QProgressBar
from PyQt5.QtCore import QTimer, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QIcon
import threading
from mutagen.mp3 import MP3  # For getting the duration of the audio file
from plc_control import user_input_queue,load_context,save_context,get_model_output,actual_position,current_velocity,plc
import pyads
 
lock = threading.Lock()

class LLMThread(QThread):
    """Thread to handle interaction with LLM."""
    update_response = pyqtSignal(str)  # Define signal to update response
    finalize_response = pyqtSignal(str)  # Define signal to finalize response

    def __init__(self, prompt, conversation):
        super().__init__()
        self.prompt = prompt
        self.conversation = conversation

    def run(self):
        global LLMresponse
        """Run LLM command and update response in real-time."""
        # Construct the full conversation prompt
        LLMresponse = get_model_output(self.prompt,self.conversation)
        self.finalize_response.emit(LLMresponse)


class MainWindow(QMainWindow):
    """Main window class."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Open eRob AI lab')
        self.setGeometry(500, 50, 700, 900)
        self.setWindowIcon(QIcon("zeroerr.png"))

        # Main layout
        main_layout = QVBoxLayout()

        # Header area
        header_label = QLabel("Chat with eRob")
        header_label.setAlignment(Qt.AlignCenter)
        header_label.setStyleSheet("font-family: 'Arial'; font-size: 24px; font-weight: bold; color: #2E4053; margin-bottom: 20px;")
        main_layout.addWidget(header_label)

        # Create a scroll area to hold messages
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setStyleSheet("background-color: #E3F2FD; border-radius: 10px;")

        self.chat_widget = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_widget)
        self.chat_layout.setAlignment(Qt.AlignTop)
        self.chat_layout.setSpacing(10)
        self.scroll_area.setWidget(self.chat_widget)

        main_layout.addWidget(self.scroll_area)

        # Input box and send button
        input_layout = QHBoxLayout()
        self.user_input = QLineEdit()
        self.user_input.setFixedHeight(50)
        self.user_input.setPlaceholderText("Type your message here...")
        self.user_input.setStyleSheet("font-family: 'Arial'; font-size: 16px; padding: 10px; border: 2px solid #BBDEFB; border-radius: 10px; background-color: #E3F2FD;")
        
        self.send_button = QPushButton("\u2328\ufe0f Send")
        self.send_button.setStyleSheet("font-family: 'Arial'; font-size: 16px; padding: 10px; background-color: #64B5F6; color: white; border-radius: 10px;")
        self.send_button.setCursor(Qt.PointingHandCursor)
        self.send_button.clicked.connect(self.send_message)
        self.user_input.returnPressed.connect(self.send_message)
        
        # Add voice input button
        self.voice_button = QPushButton("\ud83c\udfa4 Speak (English)")
        self.voice_button.setStyleSheet("font-family: 'Arial'; font-size: 16px; padding: 10px; background-color: #64B5F6; color: white; border-radius: 10px;")
        self.voice_button.setCursor(Qt.PointingHandCursor)
        self.voice_button.clicked.connect(self.start_voice_input)

        # Add Chinese voice input button
        self.chinese_voice_button = QPushButton("\ud83c\udFA4 Speak (中文)")
        self.chinese_voice_button.setStyleSheet("font-family: 'Arial'; font-size: 16px; padding: 10px; background-color: #64B5F6; color: white; border-radius: 10px;")
        self.chinese_voice_button.setCursor(Qt.PointingHandCursor)
        self.chinese_voice_button.clicked.connect(self.start_chinese_voice_input)

        input_layout.addWidget(self.user_input)
        input_layout.addWidget(self.send_button)
        input_layout.addWidget(self.voice_button)  # Add English voice button to layout
        input_layout.addWidget(self.chinese_voice_button)  # Add Chinese voice button to layout

        main_layout.addLayout(input_layout)

        # Volume bar for voice input
        self.volume_bar = QProgressBar()
        self.volume_bar.setRange(0, 100)
        self.volume_bar.setTextVisible(False)
        self.volume_bar.setStyleSheet("QProgressBar { border: 2px solid #BBDEFB; border-radius: 5px; background: #E3F2FD; } QProgressBar::chunk { background: #64B5F6; }")
        main_layout.addWidget(self.volume_bar)

        # Set the central widget of the main window
        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        # Load previous conversation context
        self.context_data = load_context()
        self.conversation = self.context_data.get("conversation", [])

        # Display previous conversation
        self.display_previous_conversation()
                # 创建位置和速度的标签
        self.position_label = QLabel("位置: 0")  # 创建位置标签
        self.position_label.setStyleSheet("font-family: 'Arial'; font-size: 16px; margin: 10px;")
        main_layout.addWidget(self.position_label)  # 添加到主布局

        self.speed_label = QLabel("速度: 0")  # 创建速度标签
        self.speed_label.setStyleSheet("font-family: 'Arial'; font-size: 16px; margin: 10px;")
        main_layout.addWidget(self.speed_label)  # 添加到主布局
                # 启动定时器以定期更新电机状态
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_motor_status)  # 连接定时器到更新函数
        self.timer.start(500)  # 每1000毫秒（1秒）更新一次
        
    def refresh_motor_status(self):
        """获取电机位置和速度并更新显示框。"""
        # 这里您需要获取实际的电机位置和速度
        position = self.get_motor_position()  # 假设有一个方法获取位置
        speed = self.get_motor_speed()  # 假设有一个方法获取速度
        self.update_motor_status(position, speed)  # 更新显示框

    def get_motor_position(self):
        """获取电机位置的示例方法。"""
        actual_position = plc.read_by_name("GVL.Actual_Position", pyads.PLCTYPE_DINT)

        return actual_position  # 示例返回值

    def get_motor_speed(self):
        """获取电机速度的示例方法。"""
        actual_velocity = plc.read_by_name("GVL.Actual_Velocity", pyads.PLCTYPE_DINT)# 这里应添加获取电机速度的实际代码
        return actual_velocity  # 示例返回值

    def update_motor_status(self, position, speed):
        """更新电机位置和速度的显示框。"""
        self.position_label.setText(f"位置: {position}")  # 更新位置显示
        self.speed_label.setText(f"速度: {speed}")  # 更新速度显示

    def start_chinese_voice_input(self):
        """Start Chinese voice input and convert speech to text."""
        recognizer = sr.Recognizer()
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source)
            print("Listening for Chinese...")
            #self.update_volume_bar(source, recognizer)  # Update volume bar while listening
            audio = recognizer.listen(source)

            try:
                user_message = recognizer.recognize_google(audio, language='zh-CN')  # Use Chinese language
                user_input_queue.put(user_message)
                print(f"You said (Chinese): {user_message}")
                self.user_input.setText(user_message)  # Set the recognized text to the input field
                self.send_message()  # Automatically send the message
            except sr.UnknownValueError:
                print("Sorry, I could not understand the audio.")
            except sr.RequestError as e:
                print(f"Could not request results from Google Speech Recognition service; {e}")


    def display_previous_conversation(self):
        """Display previous conversation in the chat area."""
        for entry in self.conversation:
            if entry['role'] == 'User':
                self.add_message(entry['content'], is_user=True)
            else:
                self.add_message(entry['content'], is_user=False)

    def send_message(self):
        user_message = self.user_input.text()
        if user_message:
            user_input_queue.put(user_message)
            self.add_message(user_message, is_user=True)
            self.user_input.clear()

            # Start the LLM thread to get the response
            self.llm_thread = LLMThread(user_message, self.conversation)
            self.llm_thread.finalize_response.connect(self.finalize_llm_response)
            self.llm_thread.start()

            # Save the conversation context
            self.conversation.append({"role": "User", "content": user_message})
            save_context({"conversation": self.conversation})

    def start_voice_input(self):
        """Start voice input and convert speech to text."""
        recognizer = sr.Recognizer()
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source)
            print("Listening...")
            #self.update_volume_bar(source, recognizer)  # Update volume bar while listening
            audio = recognizer.listen(source)

            try:
                user_message = recognizer.recognize_google(audio, language='en-US')  # Change language as needed
                user_input_queue.put(user_message)
                print(f"You said: {user_message}")
                self.user_input.setText(user_message)  # Set the recognized text to the input field
                self.send_message()  # Automatically send the message
            except sr.UnknownValueError:
                print("Sorry, I could not understand the audio.")
            except sr.RequestError as e:
                print(f"Could not request results from Google Speech Recognition service; {e}")

    def update_volume_bar(self, source, recognizer):
        """Update volume bar while listening."""
        def update():
            try:
                volume = recognizer.energy_threshold / 400 * 100  # Normalize volume for display
                self.volume_bar.setValue(min(int(volume), 100))
            except Exception as e:
                print(f"Error updating volume bar: {e}")

        timer = QTimer(self)
        timer.timeout.connect(update)
        timer.start(1000)  # Update every 100 ms

    def finalize_llm_response(self, response_text):
        """Display LLM response immediately and start generating voice in parallel."""
        # Display the response text immediately
        self.add_message(response_text, is_user=False)
        # Save the conversation context
        self.conversation.append({"role": "Assistant", "content": response_text})
        save_context({"conversation": self.conversation})

        # Generate and play voice for the response in a separate thread
        threading.Thread(target=self.generate_and_play_voice, args=(response_text,)).start()



    def generate_and_play_voice(self, text):
        """Convert generated text to speech and play it."""
        try:
            # Determine the language based on the content
            lang = 'en'  # Default to English

            audio_file = r"D:\Isaac_sim\python-control\LLM_isaac_QT_eRob\response.mp3"
            # Delete the existing file if it exists
            if os.path.exists(audio_file):
                os.remove(audio_file)

            tts = gTTS(text=text, lang=lang)  # Select language
            tts.save(audio_file)  # Save audio file

            # Use absolute path to play audio
            audio_file_path = os.path.abspath(audio_file)
            print(f"Playing audio file: {audio_file_path}")  # Debug information

           # Play audio in a separate thread
        # Play audio in a separate daemon thread
            # 创建一个锁
            with lock:  
                audio_thread = threading.Thread(target=playsound, args=(audio_file_path,))
                audio_thread.daemon = True  # Set as daemon thread
                audio_thread.start()
        except Exception as e:
            print(f"Error generating or playing voice: {e}")


    def add_message(self, message, is_user=False):
        if is_user:
            formatted_message = f"You: {message}"
        else:
            formatted_message = f"AI: {message}"

        message_widget = QLabel(formatted_message)
        message_widget.setWordWrap(True)
        message_widget.setTextInteractionFlags(Qt.TextSelectableByMouse)

        if is_user:
            message_widget.setStyleSheet(
                "background-color: #90CAF9; border-radius: 15px; padding: 15px; margin: 10px; font-family: 'Arial'; font-size: 16px; line-height: 1.5;"
            )
            message_widget.setAlignment(Qt.AlignRight)
        else:
            message_widget.setStyleSheet(
                "background-color: #E3F2FD; border-radius: 15px; padding: 15px; margin: 10px; font-family: 'Arial'; font-size: 16px; line-height: 1.5;"
            )
            message_widget.setAlignment(Qt.AlignLeft)
        self.chat_layout.addWidget(message_widget)

        QTimer.singleShot(0, lambda: self.scroll_area.ensureWidgetVisible(message_widget))


    def send_command(self):
        """处理发送命令"""
        command = self.user_input.text()
        if command:
            user_input_queue.put(command)  # 将命令放入队列
            self.user_input.clear()  # 清空输入框

def run_gui():
    """运行PyQt GUI"""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())



"""
if __name__ == '__main__':
    app = QApplication(sys.argv)
    main_window = MainWindow()
    main_window.show()
    sys.exit(app.exec_())
"""
