/**
 * 知识图谱实时可视化前端应用
 */

// 全局变量
let network = null;
let graphData = { nodes: [], edges: [] };
let entityTypeChart = null;
let updateCount = 0;
let ws = null;
let isConnected = false;

// 节点类型颜色映射
const NODE_COLORS = {
    'person': '#3498db',
    'organization': '#e74c3c',
    'mathematical_concept': '#9b59b6',
    'default': '#95a5a6'
};

// DOM加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    initApplication();
});

/**
 * 检查依赖库是否加载
 */
function checkDependencies() {
    const dependencies = {
        'vis': typeof vis !== 'undefined',
        'Chart': typeof Chart !== 'undefined',
        'WebSocket': typeof WebSocket !== 'undefined'
    };
    
    console.log('依赖库检查:', dependencies);
    
    let allLoaded = true;
    for (const [name, loaded] of Object.entries(dependencies)) {
        if (!loaded) {
            console.error(`依赖库未加载: ${name}`);
            addUpdateLog(`错误: ${name} 库未加载`, 'error');
            allLoaded = false;
        }
    }
    
    return allLoaded;
}

/**
 * 初始化应用
 */
function initApplication() {
    console.log('初始化知识图谱可视化应用...');
    addUpdateLog('应用初始化开始', 'info');
    
    // 检查依赖库
    if (!checkDependencies()) {
        addUpdateLog('部分依赖库未加载，请检查网络连接', 'error');
        updateSystemStatus('依赖库加载失败');
        return;
    }
    
    // 初始化UI组件
    initUIComponents();
    
    // 初始化图表
    initEntityTypeChart();
    
    // 连接WebSocket
    connectWebSocket();
    
    // 加载初始数据
    loadInitialData();
    
    // 设置定时器检查连接状态
    setInterval(checkConnectionStatus, 5000);
    
    console.log('应用初始化完成');
    addUpdateLog('应用初始化完成', 'success');
}

/**
 * 初始化UI组件
 */
function initUIComponents() {
    // 置信度滑块
    const confidenceSlider = document.getElementById('confidence-filter');
    const confidenceValue = document.getElementById('confidence-value');
    
    confidenceSlider.addEventListener('input', function() {
        confidenceValue.textContent = this.value;
        filterGraphByConfidence(parseFloat(this.value));
    });
    
    // 搜索功能
    document.getElementById('search-btn').addEventListener('click', searchNode);
    document.getElementById('search-node').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') searchNode();
    });
    
    // 刷新按钮
    document.getElementById('refresh-btn').addEventListener('click', loadInitialData);
    
    // 重置视图按钮
    document.getElementById('reset-view-btn').addEventListener('click', resetNetworkView);
    
    // 图控制按钮
    document.getElementById('toggle-physics').addEventListener('click', togglePhysics);
    document.getElementById('toggle-labels').addEventListener('click', toggleLabels);
    document.getElementById('export-btn').addEventListener('click', exportGraphImage);
}

/**
 * 初始化实体类型图表
 */
function initEntityTypeChart() {
    const ctx = document.getElementById('entity-type-chart').getContext('2d');
    entityTypeChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: [],
            datasets: [{
                data: [],
                backgroundColor: Object.values(NODE_COLORS),
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom'
                }
            }
        }
    });
}

/**
 * 连接WebSocket
 */
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    
    console.log(`尝试连接WebSocket: ${wsUrl}`);
    addUpdateLog(`连接WebSocket: ${wsUrl}`, 'info');
    
    try {
        ws = new WebSocket(wsUrl);
        
        ws.onopen = function() {
            console.log('WebSocket连接已建立');
            updateConnectionStatus(true);
            addUpdateLog('WebSocket连接成功', 'success');
        };
        
        ws.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);
                console.log('收到WebSocket消息:', data.type);
                handleWebSocketMessage(data);
            } catch (e) {
                console.error('解析WebSocket消息失败:', e);
                addUpdateLog('解析WebSocket消息失败', 'error');
            }
        };
        
        ws.onclose = function(event) {
            console.log('WebSocket连接已关闭', event.code, event.reason);
            updateConnectionStatus(false);
            addUpdateLog(`WebSocket连接断开 (代码: ${event.code})，5秒后重连...`, 'error');
            
            // 5秒后尝试重新连接
            setTimeout(connectWebSocket, 5000);
        };
        
        ws.onerror = function(error) {
            console.error('WebSocket错误:', error);
            addUpdateLog('WebSocket连接错误，检查后端服务是否运行', 'error');
        };
    } catch (error) {
        console.error('创建WebSocket失败:', error);
        addUpdateLog('创建WebSocket连接失败', 'error');
        updateConnectionStatus(false);
        
        // 10秒后重试
        setTimeout(connectWebSocket, 10000);
    }
}

/**
 * 处理WebSocket消息
 */
function handleWebSocketMessage(data) {
    switch (data.type) {
        case 'initial_data':
            handleInitialData(data);
            break;
        case 'data_updated':
            handleDataUpdated(data);
            break;
        default:
            console.log('未知消息类型:', data.type);
    }
}

/**
 * 处理初始数据
 */
function handleInitialData(data) {
    console.log('收到初始数据:', data);
    updateStats(data.stats);
    updateGraphData(data.graph);
    updateEntityTypeChart(data.stats.entity_types);
    addUpdateLog('初始数据加载完成', 'success');
}

/**
 * 处理数据更新
 */
function handleDataUpdated(data) {
    console.log('数据已更新:', data);
    updateCount++;
    
    // 更新统计显示
    document.getElementById('stat-updates').textContent = updateCount;
    document.getElementById('last-update').textContent = `最后更新: ${new Date().toLocaleTimeString()}`;
    
    // 重新加载数据
    loadInitialData();
    
    // 添加更新日志
    const changeTypeMap = {
        'created': '创建',
        'modified': '修改',
        'deleted': '删除'
    };
    const changeText = changeTypeMap[data.change_type] || data.change_type;
    addUpdateLog(`检测到文件${changeText}: ${data.file_path}`, 'new');
    
    // 添加视觉反馈
    document.getElementById('knowledge-graph').classList.add('pulse');
    setTimeout(() => {
        document.getElementById('knowledge-graph').classList.remove('pulse');
    }, 1000);
}

/**
 * 加载初始数据（通过REST API）
 */
async function loadInitialData() {
    try {
        updateSystemStatus('正在加载数据...');
        
        // 获取统计信息
        const statsResponse = await fetch('/api/stats');
        const stats = await statsResponse.json();
        updateStats(stats);
        
        // 获取图数据
        const graphResponse = await fetch('/api/graph');
        const graph = await graphResponse.json();
        updateGraphData(graph);
        
        // 更新实体类型图表
        updateEntityTypeChart(stats.entity_types);
        
        updateSystemStatus('数据加载完成');
        addUpdateLog('通过REST API加载数据成功', 'success');
        
    } catch (error) {
        console.error('加载数据失败:', error);
        updateSystemStatus('数据加载失败');
        addUpdateLog(`数据加载失败: ${error.message}`, 'error');
    }
}

/**
 * 更新统计信息显示
 */
function updateStats(stats) {
    document.getElementById('stat-nodes').textContent = stats.total_entities || 0;
    document.getElementById('stat-edges').textContent = stats.total_relations || 0;
    document.getElementById('stat-scenes').textContent = stats.total_scenes || 0;
    
    // 更新API状态
    document.getElementById('api-status').textContent = '已连接';
    document.getElementById('api-status').style.color = '#27ae60';
}

/**
 * 更新图数据
 */
function updateGraphData(data) {
    graphData = data;
    
    // 更新节点类型过滤选项
    updateNodeTypeFilter(data.nodes);
    
    // 创建或更新网络图
    if (!network) {
        createNetwork(data);
    } else {
        network.setData(data);
    }
}

/**
 * 创建网络图
 */
function createNetwork(data) {
    const container = document.getElementById('knowledge-graph');
    
    // 配置选项
    const options = {
        nodes: {
            shape: 'dot',
            size: 20,
            font: {
                size: 14,
                face: 'Tahoma'
            },
            borderWidth: 2
        },
        edges: {
            width: 2,
            font: {
                size: 12,
                align: 'middle'
            },
            arrows: {
                to: { enabled: true, scaleFactor: 0.5 }
            },
            smooth: {
                type: 'continuous'
            }
        },
        physics: {
            enabled: true,
            stabilization: {
                iterations: 100
            }
        },
        interaction: {
            hover: true,
            tooltipDelay: 200
        }
    };
    
    // 处理节点样式
    const processedNodes = data.nodes.map(node => {
        const color = NODE_COLORS[node.type] || NODE_COLORS.default;
        return {
            ...node,
            color: {
                background: color,
                border: '#2c3e50',
                highlight: {
                    background: color,
                    border: '#2c3e50'
                }
            },
            shapeProperties: {
                useBorderWithImage: true
            }
        };
    });
    
    // 处理边样式
    const processedEdges = data.edges.map(edge => {
        return {
            ...edge,
            color: {
                color: '#7f8c8d',
                highlight: '#3498db'
            }
        };
    });
    
    // 创建网络
    network = new vis.Network(container, {
        nodes: new vis.DataSet(processedNodes),
        edges: new vis.DataSet(processedEdges)
    }, options);
    
    // 添加事件监听
    network.on('click', function(params) {
        handleNetworkClick(params);
    });
    
    network.on('doubleClick', function(params) {
        handleNetworkDoubleClick(params);
    });
}

/**
 * 处理网络图点击事件
 */
function handleNetworkClick(params) {
    if (params.nodes.length > 0) {
        const nodeId = params.nodes[0];
        const node = graphData.nodes.find(n => n.id === nodeId);
        if (node) {
            displaySelectedInfo(node, 'node');
        }
    } else if (params.edges.length > 0) {
        const edgeId = params.edges[0];
        const edge = graphData.edges.find(e => e.id === edgeId);
        if (edge) {
            displaySelectedInfo(edge, 'edge');
        }
    }
}

/**
 * 处理网络图双击事件
 */
function handleNetworkDoubleClick(params) {
    if (params.nodes.length > 0) {
        const nodeId = params.nodes[0];
        // 聚焦到该节点
        network.focus(nodeId, {
            scale: 1.5,
            animation: {
                duration: 1000,
                easingFunction: 'easeInOutQuad'
            }
        });
    }
}

/**
 * 显示选中信息
 */
function displaySelectedInfo(item, type) {
    let info = '';
    
    if (type === 'node') {
        info = `节点ID: ${item.id}\n`;
        info += `类型: ${item.type}\n`;
        info += `置信度: ${item.confidence}\n`;
        if (item.scenes) {
            info += `出现在 ${item.scenes.length} 个场景中\n`;
        }
    } else if (type === 'edge') {
        info = `关系: ${item.label}\n`;
        info += `从: ${item.from}\n`;
        info += `到: ${item.to}\n`;
        info += `置信度: ${item.confidence}\n`;
        info += `场景: ${item.scene_id || '未知'}\n`;
    }
    
    document.getElementById('selected-info').textContent = info;
}

/**
 * 更新实体类型图表
 */
function updateEntityTypeChart(entityTypes) {
    if (!entityTypeChart) return;
    
    const labels = Object.keys(entityTypes);
    const data = Object.values(entityTypes);
    
    entityTypeChart.data.labels = labels;
    entityTypeChart.data.datasets[0].data = data;
    entityTypeChart.update();
}

/**
 * 更新节点类型过滤选项
 */
function updateNodeTypeFilter(nodes) {
    const filterSelect = document.getElementById('node-type-filter');
    const types = new Set(nodes.map(node => node.type));
    
    // 清空现有选项（保留"全部类型"）
    while (filterSelect.options.length > 1) {
        filterSelect.remove(1);
    }
    
    // 添加类型选项
    types.forEach(type => {
        const option = document.createElement('option');
        option.value = type;
        option.textContent = type;
        filterSelect.appendChild(option);
    });
    
    // 添加过滤事件
    filterSelect.addEventListener('change', function() {
        const selectedTypes = Array.from(this.selectedOptions).map(opt => opt.value);
        filterGraphByType(selectedTypes);
    });
}

/**
 * 根据置信度过滤图
 */
function filterGraphByConfidence(threshold) {
    if (!network) return;
    
    const filteredNodes = graphData.nodes.filter(node => node.confidence >= threshold);
    const filteredEdges = graphData.edges.filter(edge => edge.confidence >= threshold);
    
    network.setData({
        nodes: filteredNodes,
        edges: filteredEdges
    });
}

/**
 * 根据类型过滤图
 */
function filterGraphByType(selectedTypes) {
    if (!network) return;
    
    // 如果选择了"全部类型"或没有选择任何类型，显示所有节点
    if (selectedTypes.includes('all') || selectedTypes.length === 0) {
        network.setData(graphData);
        return;
    }
    
    // 过滤节点
    const filteredNodes = graphData.nodes.filter(node => selectedTypes.includes(node.type));
    const filteredNodeIds = new Set(filteredNodes.map(node => node.id));
    
    // 过滤边（只保留两端节点都在过滤后集合中的边）
    const filteredEdges = graphData.edges.filter(edge => 
        filteredNodeIds.has(edge.from) && filteredNodeIds.has(edge.to)
    );
    
    network.setData({
        nodes: filteredNodes,
        edges: filteredEdges
    });
}

/**
 * 搜索节点
 */
function searchNode() {
    const searchInput = document.getElementById('search-node');
    const nodeId = searchInput.value.trim();
    
    if (!nodeId) {
        alert('请输入节点ID');
        return;
    }
    
    // 在图中查找节点
    const node = graphData.nodes.find(n => n.id.toLowerCase().includes(nodeId.toLowerCase()));
    
    if (node) {
        // 聚焦到该节点
        network.focus(node.id, {
            scale: 2,
            animation: {
                duration: 1000,
                easingFunction: 'easeInOutQuad'
            }
        });
        
        // 高亮显示
        network.selectNodes([node.id]);
        
        // 显示节点信息
        displaySelectedInfo(node, 'node');
        
        addUpdateLog(`找到节点: ${node.id}`, 'success');
    } else {
        alert(`未找到节点: ${nodeId}`);
        addUpdateLog(`未找到节点: ${nodeId}`, 'error');
    }
}

/**
 * 重置网络视图
 */
function resetNetworkView() {
    if (network) {
        network.fit({
            animation: {
                duration: 1000,
                easingFunction: 'easeInOutQuad'
            }
        });
        addUpdateLog('视图已重置', 'info');
    }
}

/**
 * 切换物理引擎
 */
function togglePhysics() {
    if (!network) return;
    
    const options = network.getOptions();
    const newPhysicsState = !options.physics.enabled;
    
    network.setOptions({
        physics: { enabled: newPhysicsState }
    });
    
    const button = document.getElementById('toggle-physics');
    button.textContent = `物理引擎: ${newPhysicsState ? '开启' : '关闭'}`;
    
    addUpdateLog(`物理引擎 ${newPhysicsState ? '开启' : '关闭'}`, 'info');
}

/**
 * 切换标签显示
 */
function toggleLabels() {
    if (!network) return;
    
    const options = network.getOptions();
    const newLabelState = options.nodes.font.size === 0 ? 14 : 0;
    
    network.setOptions({
        nodes: { font: { size: newLabelState } }
    });
    
    const button = document.getElementById('toggle-labels');
    button.textContent = `显示标签: ${newLabelState > 0 ? '开启' : '关闭'}`;
    
    addUpdateLog(`标签显示 ${newLabelState > 0 ? '开启' : '关闭'}`, 'info');
}

/**
 * 导出图图片
 */
function exportGraphImage() {
    if (!network) return;
    
    const container = document.getElementById('knowledge-graph');
    const canvas = container.querySelector('canvas');
    
    if (!canvas) {
        alert('无法获取画布');
        return;
    }
    
    const link = document.createElement('a');
    link.download = `knowledge-graph-${new Date().toISOString().slice(0, 10)}.png`;
    link.href = canvas.toDataURL('image/png');
    link.click();
    
    addUpdateLog('图已导出为PNG图片', 'success');
}

/**
 * 更新连接状态
 */
function updateConnectionStatus(connected) {
    isConnected = connected;
    const statusElement = document.getElementById('connection-status');
    
    if (connected) {
        statusElement.textContent = '● 已连接';
        statusElement.className = 'status-connected';
    } else {
        statusElement.textContent = '● 连接断开';
        statusElement.className = 'status-disconnected';
    }
}

/**
 * 更新系统状态
 */
function updateSystemStatus(status) {
    document.getElementById('system-status').textContent = status;
}

/**
 * 添加更新日志
 */
function addUpdateLog(message, type = 'info') {
    const updatesList = document.getElementById('updates-list');
    const updateItem = document.createElement('div');
    
    updateItem.className = `update-item ${type}`;
    updateItem.innerHTML = `
        <span class="update-time">${new Date().toLocaleTimeString()}</span>
        <span class="update-message">${message}</span>
    `;
    
    // 添加到列表顶部
    updatesList.insertBefore(updateItem, updatesList.firstChild);
    
    // 限制日志数量
    while (updatesList.children.length > 20) {
        updatesList.removeChild(updatesList.lastChild);
    }
}

/**
 * 检查连接状态
 */
function checkConnectionStatus() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        updateConnectionStatus(true);
    } else {
        updateConnectionStatus(false);
    }
}

// 导出全局函数供HTML调用
window.searchNode = searchNode;
window.resetNetworkView = resetNetworkView;
window.togglePhysics = togglePhysics;
window.toggleLabels = toggleLabels;
window.exportGraphImage = exportGraphImage;