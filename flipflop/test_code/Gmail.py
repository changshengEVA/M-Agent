import imaplib
import email
from email.header import decode_header

# 你的 Gmail 账户信息
EMAIL_ACCOUNT = "changshengEVA@gmail.com"
EMAIL_PASSWORD = "odlratkmfvritenm"

# 连接到 Gmail 的 IMAP 服务器
IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993

def connect_gmail():
    """ 连接到 Gmail 服务器 """
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
    mail.select("inbox")  # 选择收件箱
    return mail

def fetch_latest_email(mail):
    """ 获取最新的一封邮件 """
    _, messages = mail.search(None, "ALL")
    message_ids = messages[0].split()

    if not message_ids:
        print("📭 没有找到邮件！")
        return None

    latest_email_id = message_ids[-1]  # 获取最新邮件的 ID
    _, msg_data = mail.fetch(latest_email_id, "(RFC822)")
    
    for response_part in msg_data:
        if isinstance(response_part, tuple):
            msg = email.message_from_bytes(response_part[1])
            return msg
    return None

def parse_email(msg):
    """ 解析邮件的发件人、主题和正文 """
    if not msg:
        return
    
    # 获取发件人
    from_email = msg.get("From")
    
    # 解码主题
    subject, encoding = decode_header(msg["Subject"])[0]
    if isinstance(subject, bytes):
        subject = subject.decode(encoding if encoding else "utf-8")

    print(f"📧 发件人: {from_email}")
    print(f"📌 主题: {subject}")

    # 获取邮件正文
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))

            if content_type == "text/plain" and "attachment" not in content_disposition:
                body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                print(f"📄 正文:\n{body}")
                break
    else:
        body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
        print(f"📄 正文:\n{body}")

def main():
    """ 主函数 """
    mail = connect_gmail()
    msg = fetch_latest_email(mail)
    parse_email(msg)
    mail.logout()

if __name__ == "__main__":
    main()
