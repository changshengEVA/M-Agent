# 三维知识图谱可视化 - 摄像机控制指南

## 概述

本三维可视化项目使用Three.js的OrbitControls库来实现摄像机控制。摄像机控制允许用户自由探索三维知识图谱，包括旋转、平移、缩放和聚焦等功能。

## 摄像机初始位置

摄像机初始配置如下（位于 [`three-scene.js`](kg_visualization/3d_frontend/js/three-scene.js:82-84)）：
```javascript
camera.position.set(200, 200, 300);  // 从斜上方观察
camera.lookAt(0, 0, 100);           // 看向中间层（特征层）
```

## 鼠标控制方法

### 1. 旋转视图
- **左键拖动**：按住鼠标左键并拖动可以旋转摄像机，围绕场景中心点旋转
- **旋转范围**：可以360度全方位旋转，包括从顶部到底部的完整视角

### 2. 平移视图
- **右键拖动**：按住鼠标右键并拖动可以平移整个场景
- **中键拖动**：同样支持平移功能
- **屏幕空间平移**：启用`screenSpacePanning: true`，平移操作在屏幕空间进行

### 3. 缩放视图
- **鼠标滚轮**：向上滚动放大，向下滚动缩小
- **缩放限制**：最小距离30单位，最大距离1500单位

### 4. 双击聚焦
- **双击节点**：双击任意节点可以自动将摄像机移动到该节点的斜上方位置
- **平滑动画**：摄像机移动使用缓动动画，过渡平滑自然

## 键盘控制方法

OrbitControls默认支持以下键盘控制：
- **箭头键**：平移视图
- **Page Up/Page Down**：缩放视图
- **Home键**：重置视图到初始位置

## UI控制面板功能

### 1. 重置视图按钮
- **位置**：控制面板中的"🗺️ 重置视图"按钮
- **功能**：将摄像机重置到初始位置 `(200, 200, 300)`，焦点重置到 `(0, 0, 100)`

### 2. 自动旋转
- **位置**：三维视图上方的"🔄 自动旋转"按钮
- **功能**：开启/关闭场景自动旋转，旋转速度可在配置中调整

### 3. 搜索聚焦
- **位置**：控制面板中的搜索功能
- **功能**：搜索节点后，自动将摄像机聚焦到第一个匹配的节点

### 4. 节点点击聚焦
- **操作**：双击任意节点
- **功能**：摄像机平滑移动到节点的斜上方位置，焦点对准该节点

## 摄像机控制配置

在 [`three-scene.js`](kg_visualization/3d_frontend/js/three-scene.js:95-107) 中的OrbitControls配置：

```javascript
this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
this.controls.enableDamping = true;           // 启用阻尼效果，移动更平滑
this.controls.dampingFactor = 0.05;           // 阻尼系数
this.controls.screenSpacePanning = true;      // 屏幕空间平移
this.controls.enablePan = true;               // 启用平移
this.controls.enableRotate = true;            // 启用旋转
this.controls.enableZoom = true;              // 启用缩放
this.controls.minDistance = 30;               // 最小缩放距离
this.controls.maxDistance = 1500;             // 最大缩放距离
this.controls.maxPolarAngle = Math.PI;        // 允许完全旋转（包括从顶部查看）
this.controls.minPolarAngle = 0;              // 允许从顶部查看
this.controls.target.set(0, 0, 100);          // 设置初始焦点（特征层）
```

## 焦点控制方法

### 1. 程序化焦点控制
可以通过代码控制摄像机焦点：

```javascript
// 聚焦到特定节点
sceneManager.focusOnNode(nodeId);

// 直接设置摄像机位置
sceneManager.camera.position.set(x, y, z);
sceneManager.controls.target.set(targetX, targetY, targetZ);
sceneManager.controls.update();
```

### 2. 动画焦点移动
系统使用缓动动画实现平滑的焦点移动：
- **缓动函数**：`easeOutCubic` 提供自然的减速效果
- **动画时长**：1000毫秒（1秒）
- **插值计算**：线性插值摄像机位置和焦点目标

## 坐标系说明

- **X轴**：红色，水平方向（右）
- **Y轴**：绿色，水平方向（上）  
- **Z轴**：蓝色，垂直方向（前/深度）

**重要**：本项目使用Z轴作为垂直/深度轴，三层结构沿Z轴分布：
- 实体层：Z = 0
- 特征层：Z = 100
- 场景层：Z = 200

## 常见操作示例

### 查看特定层
1. 使用鼠标旋转到俯视角度
2. 调整缩放以清晰看到层结构
3. 使用平移功能移动视图

### 追踪节点关系
1. 双击实体节点聚焦
2. 观察从实体到特征的垂直连接
3. 继续追踪从特征到场景的连接

### 全局概览
1. 点击"重置视图"按钮回到初始视角
2. 调整缩放以看到整个场景
3. 开启自动旋转以动态查看

## 故障排除

### 摄像机卡住或无响应
1. 检查浏览器控制台是否有错误
2. 尝试点击"重置视图"按钮
3. 刷新页面重新初始化场景

### 无法看到特定层
1. 检查控制面板中的层显示开关
2. 调整摄像机角度和位置
3. 确认节点数据已正确加载

### 性能问题
1. 关闭性能监控面板（如果开启）
2. 减少同时显示的节点数量
3. 关闭不必要的视觉效果

## 高级配置

如需修改摄像机控制参数，可编辑 [`three-scene.js`](kg_visualization/3d_frontend/js/three-scene.js) 中的配置对象：

```javascript
this.config = {
    // ... 其他配置
    rotationSpeed: 0.002,      // 自动旋转速度
    animationSpeed: 0.05       // 动画速度
};
```

## 总结

本三维可视化系统提供了完整的摄像机控制功能，包括：
- 鼠标交互：旋转、平移、缩放
- 键盘控制：箭头键导航
- UI控制：重置、自动旋转、搜索聚焦
- 程序化控制：通过API控制摄像机位置和焦点

这些控制功能使用户能够自由探索三维知识图谱，从不同角度观察实体、特征和场景之间的关系。