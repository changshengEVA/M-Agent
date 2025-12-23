import imaplib
import email
import yaml
import os, sys
from email.header import decode_header
from llama_index.core.llms import ChatMessage
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flipflop.utils import try_multi_decode

# 读取配置文件，获取 Gmail 相关信息（注意节点名称为 Gmail）
with open("./config/email.yaml", 'r', encoding="utf-8") as f:
    cfg = yaml.safe_load(f)['Gmail']

# 从配置中读取服务器地址、账户、密码和总结提示
imap_host = cfg["url"]
username = cfg["Acount"]
password = cfg["Password"]
conclude_prompt = cfg["conclude_prompt"]

def connect_gmail():
    """连接到 Gmail 的 IMAP 服务器并选择 INBOX收件箱"""
    mail = imaplib.IMAP4_SSL(imap_host)
    mail.login(username, password)
    mail.select("INBOX")
    return mail

def read_unseen_emails(use_llm, delete=False):
    email_get = ''
    mail = connect_gmail()
    
    # 搜索未读邮件
    status, messages = mail.search(None, 'UNSEEN')
    
    def conclude(email_message):
        prompt = conclude_prompt.format(str(email_message))
        response = use_llm.chat(messages=[ChatMessage(content=prompt)])
        print(response, type(response))
        return str(response).split('assistant:')[-1]
    
    if messages:
        email_ids = messages[0].split()
        print("邮件数量:", len(email_ids))
        email_get += "邮件数量:" + str(len(email_ids))
        for email_id in email_ids:
            status, email_data = mail.fetch(email_id, '(RFC822)')
            if status == 'OK' and email_data:
                # 解析邮件
                email_message = email.message_from_bytes(email_data[0][1])
                
                # 提取并解码 Subject 与 From
                subject = decode_header(email_message['subject'])[0][0]
                from_ = decode_header(email_message['from'])[0][0]
                if isinstance(subject, bytes):
                    subject = subject.decode('utf-8', errors='ignore')
                if isinstance(from_, bytes):
                    from_ = from_.decode('utf-8', errors='ignore')
                
                content_type = email_message.get_content_type()
                
                print("\n---------------------------")
                print(f"Subject: {try_multi_decode(subject)}, From: {try_multi_decode(from_)}", subject)
                email_get += "\n---------------------------\n"
                email_get += f"Subject: {try_multi_decode(subject)}, From: {try_multi_decode(from_)}"
                print(content_type)
                
                # 检查邮件类型
                if content_type == 'multipart/alternative':
                    # 遍历邮件的各个部分
                    for part in email_message.walk():
                        sub_content_type = part.get_content_type()
                        if sub_content_type == 'text/plain':
                            plain_text = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='ignore')
                            conclude_plain_text = conclude(plain_text)
                            print("concluded:", conclude_plain_text)
                            email_get += "\n纯文本内容：" + conclude_plain_text
                        elif sub_content_type == 'text/html':
                            html_text = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='ignore')
                        # 此处仅处理 text/plain，如有需要可增加对 HTML 的处理
                elif content_type in ['text/plain', 'text/html']:
                    content = email_message.get_payload(decode=True)
                    content = content.decode('utf-8', errors='ignore')
                    if len(content) < 100:
                        conclude_content = conclude(content)
                        print("conclude:", conclude_content)
                        email_get += "\n纯文本内容：" + conclude_content
                    else:
                        print('too long to conclude')
                if delete:
                    mail.store(email_id, '+FLAGS', '\\Seen')
    
    mail.close()
    mail.logout()
    return email_get
