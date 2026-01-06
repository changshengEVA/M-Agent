// WebSocket连接管理器
class WebSocketManager {
    constructor(apiBaseUrl) {
        this.wsUrl = `ws://${apiBaseUrl.replace('http://', '')}/ws`;
        this.ws = null;
        this.isConnected = false;
        this.messageHandlers = [];
    }

    connect(onOpen, onMessage, onError, onClose) {
        try {
            this.ws = new WebSocket(this.wsUrl);
            
            this.ws.onopen = () => {
                console.log('WebSocket连接已建立');
                this.isConnected = true;
                if (onOpen) onOpen();
            };
            
            this.ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (onMessage) onMessage(data);
                this.messageHandlers.forEach(handler => handler(data));
            };
            
            this.ws.onerror = (error) => {
                console.error('WebSocket错误:', error);
                this.isConnected = false;
                if (onError) onError(error);
            };
            
            this.ws.onclose = () => {
                console.log('WebSocket连接已关闭');
                this.isConnected = false;
                if (onClose) onClose();
            };
            
        } catch (error) {
            console.error('创建WebSocket连接失败:', error);
            this.isConnected = false;
            if (onError) onError(error);
        }
    }

    addMessageHandler(handler) {
        this.messageHandlers.push(handler);
    }

    removeMessageHandler(handler) {
        const index = this.messageHandlers.indexOf(handler);
        if (index > -1) {
            this.messageHandlers.splice(index, 1);
        }
    }

    send(data) {
        if (this.ws && this.isConnected) {
            this.ws.send(JSON.stringify(data));
        } else {
            console.warn('WebSocket未连接，无法发送消息');
        }
    }

    disconnect() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
            this.isConnected = false;
        }
    }
}

export default WebSocketManager;