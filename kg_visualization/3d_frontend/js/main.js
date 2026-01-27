/**
 * 三维知识图谱可视化主应用
 * 集成所有组件并启动应用
 */

// 全局应用实例
let app = null;

class KnowledgeGraph3DApp {
    constructor() {
        // 初始化组件
        this.dataLoader = dataLoader; // 使用全局实例
        this.sceneManager = new ThreeSceneManager('knowledge-graph-3d');
        this.uiManager = new UIManager(this.dataLoader, this.sceneManager);
        
        // 应用状态
        this.isRunning = false;
        
        // 错误处理
        this._setupErrorHandling();
    }
    
    /**
     * 设置错误处理
     */
    _setupErrorHandling() {
        // 全局错误处理
        window.addEventListener('error', (event) => {
            console.error('全局错误:', event.error);
            this._handleError(event.error);
        });
        
        window.addEventListener('unhandledrejection', (event) => {
            console.error('未处理的Promise拒绝:', event.reason);
            this._handleError(event.reason);
        });
    }
    
    /**
     * 处理错误
     */
    _handleError(error) {
        // 显示错误信息
        const errorMessage = error.message || '未知错误';
        this.uiManager._addUpdateLog(`错误: ${errorMessage}`, 'error');
        
        // 更新系统状态
        this.uiManager._updateSystemStatus('发生错误');
        
        // 可以在这里添加更多错误处理逻辑，如重试、恢复等
    }
    
    /**
     * 检查依赖
     */
    _checkDependencies() {
        const dependencies = {
            'THREE': typeof THREE !== 'undefined',
            'OrbitControls': typeof THREE !== 'undefined' && typeof THREE.OrbitControls !== 'undefined',
            'WebSocket': typeof WebSocket !== 'undefined',
            'fetch': typeof fetch !== 'undefined'
        };
        
        console.log('依赖检查:', dependencies);
        
        const missingDeps = Object.entries(dependencies)
            .filter(([name, loaded]) => !loaded)
            .map(([name]) => name);
        
        if (missingDeps.length > 0) {
            throw new Error(`缺少依赖库: ${missingDeps.join(', ')}`);
        }
        
        return true;
    }
    
    /**
     * 启动应用
     */
    async start() {
        if (this.isRunning) {
            console.warn('应用已经在运行');
            return;
        }
        
        try {
            console.log('启动三维知识图谱可视化应用...');
            
            // 检查依赖
            this._checkDependencies();
            
            // 启动UI管理器
            await this.uiManager.start();
            
            this.isRunning = true;
            console.log('应用启动成功');
            
            // 添加启动日志
            this.uiManager._addUpdateLog('三维知识图谱可视化应用启动成功', 'success');
            
        } catch (error) {
            console.error('应用启动失败:', error);
            this._handleError(error);
            
            // 显示错误页面
            this._showErrorPage(error);
        }
    }
    
    /**
     * 显示错误页面
     */
    _showErrorPage(error) {
        const container = document.getElementById('knowledge-graph-3d');
        if (!container) return;
        
        const errorHtml = `
            <div style="
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                height: 100%;
                color: #e74c3c;
                text-align: center;
                padding: 20px;
                background: rgba(30, 30, 40, 0.9);
                border-radius: 10px;
            ">
                <h2 style="margin-bottom: 20px;">❌ 应用启动失败</h2>
                <p style="margin-bottom: 15px; color: #bbb;">${error.message || '未知错误'}</p>
                <div style="margin-bottom: 20px; padding: 15px; background: rgba(231, 76, 60, 0.1); border-radius: 5px; text-align: left;">
                    <strong>可能的原因:</strong>
                    <ul style="margin: 10px 0; padding-left: 20px;">
                        <li>Three.js库加载失败</li>
                        <li>后端服务未运行</li>
                        <li>浏览器不支持WebGL</li>
                        <li>网络连接问题</li>
                    </ul>
                </div>
                <button id="retry-btn" style="
                    padding: 10px 20px;
                    background: #3498db;
                    color: white;
                    border: none;
                    border-radius: 5px;
                    cursor: pointer;
                    font-size: 16px;
                ">重试</button>
            </div>
        `;
        
        container.innerHTML = errorHtml;
        
        // 添加重试按钮事件
        document.getElementById('retry-btn').addEventListener('click', () => {
            location.reload();
        });
    }
    
    /**
     * 停止应用
     */
    stop() {
        if (!this.isRunning) return;
        
        console.log('停止应用...');
        
        // 销毁UI管理器
        this.uiManager.destroy();
        
        this.isRunning = false;
        console.log('应用已停止');
    }
    
    /**
     * 获取应用状态
     */
    getStatus() {
        return {
            isRunning: this.isRunning,
            connectionStatus: this.dataLoader.getConnectionStatus(),
            sceneInitialized: this.sceneManager.isInitialized
        };
    }
    
    /**
     * 重新加载数据
     */
    async reloadData() {
        if (!this.isRunning) {
            console.warn('应用未运行，无法重新加载数据');
            return;
        }
        
        try {
            this.uiManager._updateSystemStatus('重新加载数据...');
            await this.uiManager._loadAllData();
            this.uiManager._addUpdateLog('数据重新加载完成', 'success');
        } catch (error) {
            console.error('重新加载数据失败:', error);
            this._handleError(error);
        }
    }
}

/**
 * DOM加载完成后初始化应用
 */
document.addEventListener('DOMContentLoaded', async function() {
    console.log('DOM加载完成，初始化三维知识图谱可视化应用...');
    
    try {
        // 创建应用实例
        app = new KnowledgeGraph3DApp();
        
        // 启动应用
        await app.start();
        
        // 将应用实例暴露给全局，方便调试
        window.KnowledgeGraph3DApp = app;
        
    } catch (error) {
        console.error('应用初始化失败:', error);
        
        // 显示错误信息
        const statusElement = document.getElementById('system-status');
        if (statusElement) {
            statusElement.textContent = `初始化失败: ${error.message}`;
            statusElement.style.color = '#e74c3c';
        }
    }
});

/**
 * 页面卸载时清理资源
 */
window.addEventListener('beforeunload', function() {
    if (app) {
        app.stop();
    }
});

/**
 * 导出全局函数供HTML调用
 */
window.KnowledgeGraph3D = {
    // 应用控制
    startApp: async function() {
        if (!app) {
            app = new KnowledgeGraph3DApp();
        }
        await app.start();
    },
    
    stopApp: function() {
        if (app) {
            app.stop();
        }
    },
    
    reloadData: async function() {
        if (app) {
            await app.reloadData();
        }
    },
    
    getStatus: function() {
        if (app) {
            return app.getStatus();
        }
        return { isRunning: false };
    },
    
    // 场景控制
    toggleRotation: function() {
        if (app && app.sceneManager) {
            return app.sceneManager.toggleRotation();
        }
        return false;
    },
    
    toggleGrid: function() {
        if (app && app.sceneManager) {
            return app.sceneManager.toggleGrid();
        }
        return false;
    },
    
    toggleAxes: function() {
        if (app && app.sceneManager) {
            return app.sceneManager.toggleAxes();
        }
        return false;
    },
    
    exportScreenshot: function() {
        if (app && app.sceneManager) {
            return app.sceneManager.exportScreenshot();
        }
        return false;
    },
    
    // 数据操作
    searchNode: async function(query) {
        if (app && app.dataLoader) {
            return await app.dataLoader.searchNode(query);
        }
        return [];
    },
    
    switchMemory: async function(memoryId) {
        if (app && app.dataLoader) {
            return await app.dataLoader.switchMemory(memoryId);
        }
        return null;
    },
    
    // 调试工具
    getAppInstance: function() {
        return app;
    },
    
    getDataLoader: function() {
        return app ? app.dataLoader : null;
    },
    
    getSceneManager: function() {
        return app ? app.sceneManager : null;
    },
    
    getUIManager: function() {
        return app ? app.uiManager : null;
    },
    
    // 深度调试
    debugDepthInfo: function() {
        if (!app || !app.sceneManager) {
            console.error('应用或场景管理器未初始化');
            return null;
        }
        
        try {
            // 动态加载调试工具
            const script = document.createElement('script');
            script.src = 'debug_depth.js';
            script.onload = () => {
                if (typeof DepthDebugger !== 'undefined') {
                    const debuggerInstance = new DepthDebugger(app.sceneManager);
                    const depthInfo = debuggerInstance.collectDepthInfo();
                    debuggerInstance.logReport();
                    
                    // 将深度信息暴露给控制台
                    console.log('深度调试信息:', depthInfo);
                    window.lastDepthInfo = depthInfo;
                } else {
                    console.error('DepthDebugger类未定义');
                }
            };
            script.onerror = () => {
                console.error('无法加载调试工具脚本');
                // 尝试直接使用内联代码
                _fallbackDepthDebug();
            };
            document.head.appendChild(script);
            
        } catch (error) {
            console.error('深度调试失败:', error);
            return null;
        }
        
        // 备用调试函数
        function _fallbackDepthDebug() {
            const sceneManager = app.sceneManager;
            const config = sceneManager.config;
            
            console.log('=== 深度调试信息（备用）===');
            console.log('层深度配置:');
            console.log(`- 实体层: Z = ${config.entityLayerZ}`);
            console.log(`- 特征层: Z = ${config.featureLayerZ} (距离实体层: ${config.featureLayerZ - config.entityLayerZ})`);
            console.log(`- 场景层: Z = ${config.sceneLayerZ} (距离特征层: ${config.sceneLayerZ - config.featureLayerZ})`);
            
            // 检查平面
            if (sceneManager.scene) {
                let planeCount = 0;
                sceneManager.scene.traverse((object) => {
                    if (object.isMesh && object.geometry && object.geometry.type === 'PlaneGeometry') {
                        planeCount++;
                        console.log(`平面 ${planeCount}:`);
                        console.log(`  位置: (${object.position.x.toFixed(2)}, ${object.position.y.toFixed(2)}, ${object.position.z.toFixed(2)})`);
                        console.log(`  旋转: (${object.rotation.x.toFixed(2)}, ${object.rotation.y.toFixed(2)}, ${object.rotation.z.toFixed(2)})`);
                    }
                });
            }
            
            // 检查节点
            if (sceneManager.nodes && sceneManager.nodes.size > 0) {
                console.log(`总节点数: ${sceneManager.nodes.size}`);
                
                const layerStats = {
                    entity: { count: 0, minZ: Infinity, maxZ: -Infinity },
                    feature: { count: 0, minZ: Infinity, maxZ: -Infinity },
                    scene: { count: 0, minZ: Infinity, maxZ: -Infinity }
                };
                
                sceneManager.nodes.forEach((node, nodeId) => {
                    const layer = node.layer;
                    const z = node.mesh.position.z;
                    
                    if (layerStats[layer]) {
                        layerStats[layer].count++;
                        layerStats[layer].minZ = Math.min(layerStats[layer].minZ, z);
                        layerStats[layer].maxZ = Math.max(layerStats[layer].maxZ, z);
                    }
                });
                
                console.log('各层节点深度统计:');
                Object.entries(layerStats).forEach(([layer, stats]) => {
                    if (stats.count > 0) {
                        console.log(`- ${layer}层: ${stats.count} 个节点, Z范围: ${stats.minZ.toFixed(2)} 到 ${stats.maxZ.toFixed(2)}`);
                    }
                });
            }
        }
    }
};

// 添加CSS样式
const addStyles = () => {
    const styles = `
        .node-info {
            padding: 15px;
            background: rgba(30, 30, 40, 0.6);
            border-radius: 8px;
            margin-bottom: 10px;
        }
        
        .node-info h4 {
            color: #64b5f6;
            margin-bottom: 15px;
            border-bottom: 1px solid rgba(100, 181, 246, 0.3);
            padding-bottom: 8px;
        }
        
        .info-row {
            margin-bottom: 8px;
            display: flex;
        }
        
        .info-row strong {
            min-width: 80px;
            color: #bbbbbb;
        }
        
        .info-section {
            margin-top: 15px;
            margin-bottom: 10px;
            color: #64b5f6;
            font-weight: 500;
        }
        
        .features-list, .scenes-list, .related-features-list {
            max-height: 200px;
            overflow-y: auto;
            padding-right: 10px;
        }
        
        .feature-item, .scene-item, .related-feature-item {
            padding: 8px 10px;
            margin-bottom: 5px;
            background: rgba(50, 50, 60, 0.4);
            border-radius: 5px;
            border-left: 3px solid #9b59b6;
        }
        
        .scene-item {
            border-left-color: #2ecc71;
        }
        
        .related-feature-item {
            border-left-color: #3498db;
        }
        
        .feature-index, .scene-index {
            color: #90a4ae;
            margin-right: 8px;
            font-size: 0.9em;
        }
        
        .feature-text, .scene-id, .entity-ref {
            color: #e0e0e0;
        }
        
        .scene-time {
            color: #90a4ae;
            font-size: 0.9em;
            margin-left: 8px;
        }
        
        .error {
            color: #e74c3c;
            padding: 10px;
            background: rgba(231, 76, 60, 0.1);
            border-radius: 5px;
            border-left: 3px solid #e74c3c;
        }
    `;
    
    const styleElement = document.createElement('style');
    styleElement.textContent = styles;
    document.head.appendChild(styleElement);
};

// 添加样式
addStyles();

// 添加摄像机控制事件监听器
const setupCameraControls = () => {
    // 等待DOM完全加载
    setTimeout(() => {
        // 获取滑块和显示元素
        const thetaSlider = document.getElementById('initial-angle-theta');
        const phiSlider = document.getElementById('initial-angle-phi');
        const distanceSlider = document.getElementById('initial-distance');
        const applyButton = document.getElementById('apply-camera-settings');
        
        const thetaValue = document.getElementById('theta-value');
        const phiValue = document.getElementById('phi-value');
        const distanceValue = document.getElementById('distance-value');
        
        if (!thetaSlider || !phiSlider || !distanceSlider || !applyButton) {
            console.log('摄像机控制元素未找到，将在稍后重试');
            return;
        }
        
        // 更新滑块值显示
        const updateSliderValues = () => {
            if (thetaValue) thetaValue.textContent = `${thetaSlider.value}°`;
            if (phiValue) phiValue.textContent = `${phiSlider.value}°`;
            if (distanceValue) distanceValue.textContent = distanceSlider.value;
        };
        
        // 初始更新
        updateSliderValues();
        
        // 滑块变化时更新显示
        thetaSlider.addEventListener('input', updateSliderValues);
        phiSlider.addEventListener('input', updateSliderValues);
        distanceSlider.addEventListener('input', updateSliderValues);
        
        // 应用按钮点击事件
        applyButton.addEventListener('click', () => {
            const theta = parseFloat(thetaSlider.value);
            const phi = parseFloat(phiSlider.value);
            const distance = parseFloat(distanceSlider.value);
            
            if (app && app.sceneManager) {
                app.sceneManager.setCameraPosition(theta, phi, distance);
                console.log(`应用摄像机设置: θ=${theta}°, φ=${phi}°, 距离=${distance}`);
                
                // 显示成功消息
                if (app.uiManager && app.uiManager._addUpdateLog) {
                    app.uiManager._addUpdateLog(`摄像机设置已应用: θ=${theta}°, φ=${phi}°, 距离=${distance}`, 'success');
                }
            } else {
                console.error('应用或场景管理器未初始化');
            }
        });
        
        console.log('摄像机控制事件监听器已设置');
    }, 1000); // 延迟1秒以确保DOM完全加载
};

// 在DOM加载完成后设置摄像机控制
document.addEventListener('DOMContentLoaded', () => {
    // 延迟设置摄像机控制，确保其他组件已初始化
    setTimeout(setupCameraControls, 2000);
});

// 导出
if (typeof module !== 'undefined' && module.exports) {
    module.exports = KnowledgeGraph3DApp;
    module.exports.KnowledgeGraph3D = window.KnowledgeGraph3D;
}