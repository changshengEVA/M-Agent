from observation_chat import *
import time
from datetime import datetime
print("启动前置工具...")
from flipflop.utils import *
from flipflop.ReceiveEmail import read_unseen_emails
import asyncio
def check_email_and_start_chat(args):
    # print(args)
    T = 0
    try:
        while True:
            T_c = time.time()
            if T_c - T >= 1000:
                observation = read_unseen_emails(use_llm=args.llm, delete=True)
                if observation:
                    start_chat(args.llm, args.embed_model, args.memory, args.store, observation)
                else:
                    print("未检测到邮件...")
                T = T_c  # 更新时间戳
            time.sleep(100)
    except KeyboardInterrupt:
        if args.store is False:
            print("服务器已停止...")
        else:
            print("服务器已停止,正在记录index中...")
            store_mem(args.embed_model, args.llm)
            

def start_server(args):
    T = time.time()
    print("[{}]".format(get_current_time()), "启动服务器...")
    print("邮件监视器启动...")
    check_email_and_start_chat(args)
        
if __name__ == "__main__":
    start_server()