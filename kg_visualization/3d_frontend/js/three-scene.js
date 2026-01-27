/**
 * Three.js三维场景管理器
 * 负责创建和管理三维知识图谱场景
 */

class ThreeSceneManager {
    constructor(containerId) {
        this.containerId = containerId;
        this.container = document.getElementById(containerId);
        
        // Three.js核心组件
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.controls = null;
        this.stats = null;
        
        // 场景对象
        this.entityLayer = null;
        this.featureLayer = null;
        this.sceneLayer = null;
        
        // 节点和边集合
        this.nodes = new Map(); // id -> {mesh, label, data}
        this.edges = new Map(); // id -> {line, data}
        
        // 状态
        this.isInitialized = false;
        this.isRotating = false;
        this.showGrid = false; // 默认关闭网格
        this.showAxes = false; // 默认关闭坐标轴
        
        // 配置
        this.config = {
            // 层高度（使用Z轴作为垂直轴，蓝色轴）
            entityLayerZ: 0,
            featureLayerZ: 100,
            sceneLayerZ: 200,
            
            // 节点大小
            entityNodeSize: 8,
            featureNodeSize: 6,
            sceneNodeSize: 4,
            
            // 颜色 - 使用更亮的颜色以提高可见性
            backgroundColor: 0x0a0a1a, // 稍亮的深蓝色背景
            gridColor: 0x666688,       // 更亮的网格颜色
            axisColors: {
                x: 0xff6666,    // 更亮的红色 = X轴（右）
                y: 0x66ff66,    // 更亮的绿色 = Y轴（上）
                z: 0x6666ff     // 更亮的蓝色 = Z轴（前/垂直）
            },
            
            // 节点默认颜色（更亮的颜色）
            entityColor: 0x1E90FF,    // 道奇蓝 - 更亮的蓝色
            featureColor: 0xFF69B4,   // 热粉色 - 更亮的粉色
            sceneColor: 0x32CD32,     // 酸橙绿 - 更亮的绿色
            
            // 平面颜色（与节点颜色对应，但保持低透明度）
            entityPlaneColor: 0x1E90FF,  // 道奇蓝
            featurePlaneColor: 0xFF69B4, // 热粉色
            scenePlaneColor: 0x32CD32,   // 酸橙绿
            
            // 光照
            ambientLightColor: 0x505050, // 更亮的环境光
            directionalLightColor: 0xffffff,
            
            // 动画
            rotationSpeed: 0.002,
            animationSpeed: 0.05
        };
    }
    
    /**
     * 初始化Three.js场景
     */
    init() {
        if (this.isInitialized) {
            console.warn('场景已经初始化');
            return;
        }
        
        try {
            // 1. 创建场景
            this.scene = new THREE.Scene();
            this.scene.background = new THREE.Color(this.config.backgroundColor);
            
            // 2. 创建相机（使用用户提供的摄像机信息）
            const width = this.container.clientWidth;
            const height = this.container.clientHeight;
            
            this.camera = new THREE.PerspectiveCamera(60, width / height, 1, 5000);
            // 使用用户提供的摄像机位置
            this.camera.position.set(3.6, -403.7, -71.5);
            
            // 3. 创建渲染器
            this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
            this.renderer.setSize(width, height);
            this.renderer.setPixelRatio(window.devicePixelRatio);
            this.renderer.shadowMap.enabled = true;
            this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
            
            this.container.appendChild(this.renderer.domElement);
            
            // 4. 创建控制器 - 改进摄像机控制
            this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
            this.controls.enableDamping = true;
            this.controls.dampingFactor = 0.05;
            this.controls.screenSpacePanning = true; // 允许屏幕空间平移
            this.controls.enablePan = true; // 启用平移
            this.controls.enableRotate = true; // 启用旋转
            this.controls.enableZoom = true; // 启用缩放
            this.controls.minDistance = 30;
            this.controls.maxDistance = 1500;
            this.controls.maxPolarAngle = Math.PI; // 允许完全旋转
            this.controls.minPolarAngle = 0; // 允许从顶部查看
            // 使用用户提供的目标位置
            this.controls.target.set(2.3, -51.4, 126.5);
            
            // 5. 添加光照
            this._setupLights();
            
            // 6. 添加辅助工具
            if (this.showGrid) {
                this._addGridHelper();
            }
            
            if (this.showAxes) {
                this._addAxesHelper();
            }
            
            // 7. 创建层
            this._createLayers();
            
            // 8. 初始化性能监控
            this._initStats();
            
            // 9. 绑定事件
            this._bindEvents();
            
            // 10. 开始渲染循环
            this.animate();
            
            this.isInitialized = true;
            console.log('Three.js场景初始化完成');
            
        } catch (error) {
            console.error('初始化Three.js场景失败:', error);
            throw error;
        }
    }
    
    /**
     * 设置光照
     */
    _setupLights() {
        // 环境光
        const ambientLight = new THREE.AmbientLight(this.config.ambientLightColor, 0.6);
        this.scene.add(ambientLight);
        
        // 平行光（主光源）
        const directionalLight = new THREE.DirectionalLight(this.config.directionalLightColor, 0.8);
        directionalLight.position.set(100, 100, 200);
        directionalLight.castShadow = true;
        directionalLight.shadow.mapSize.width = 2048;
        directionalLight.shadow.mapSize.height = 2048;
        this.scene.add(directionalLight);
        
        // 补光
        const fillLight = new THREE.DirectionalLight(0x6666ff, 0.3);
        fillLight.position.set(-100, -100, -100);
        this.scene.add(fillLight);
    }
    
    /**
     * 添加网格辅助（在XY平面上）
     */
    _addGridHelper() {
        const gridHelper = new THREE.GridHelper(400, 40, this.config.gridColor, this.config.gridColor);
        gridHelper.position.z = -50;
        this.scene.add(gridHelper);
        this.gridHelper = gridHelper;
    }
    
    /**
     * 添加坐标轴辅助（Three.js默认：X=红色（右），Y=绿色（上），Z=蓝色（前/垂直））
     */
    _addAxesHelper() {
        const axesHelper = new THREE.AxesHelper(100);
        axesHelper.position.z = -50;
        this.scene.add(axesHelper);
        this.axesHelper = axesHelper;
    }
    
    /**
     * 创建三层结构（使用Z轴作为垂直轴）
     * 注意：层组的位置Z设置为0，节点的Z坐标由布局算法直接决定
     * 这样可以避免双重Z坐标叠加的问题
     */
    _createLayers() {
        // 实体层
        this.entityLayer = new THREE.Group();
        this.entityLayer.name = 'entityLayer';
        this.entityLayer.position.z = 0; // 层组位置为0，节点位置决定实际深度
        this.scene.add(this.entityLayer);
        
        // 特征层
        this.featureLayer = new THREE.Group();
        this.featureLayer.name = 'featureLayer';
        this.featureLayer.position.z = 0; // 层组位置为0，节点位置决定实际深度
        this.scene.add(this.featureLayer);
        
        // 场景层
        this.sceneLayer = new THREE.Group();
        this.sceneLayer.name = 'sceneLayer';
        this.sceneLayer.position.z = 0; // 层组位置为0，节点位置决定实际深度
        this.scene.add(this.sceneLayer);
        
        // 添加层平面（半透明）
        this._addLayerPlanes();
    }
    
    /**
     * 添加层平面（可视化层边界，使用Z轴作为垂直轴）
     * 平面在XY平面上，法向量指向Z轴（深度轴）
     */
    _addLayerPlanes() {
        // 实体层平面（水平面，在XY平面上，法向量指向Z轴）
        const entityPlaneGeometry = new THREE.PlaneGeometry(350, 350);
        const entityPlaneMaterial = new THREE.MeshBasicMaterial({
            color: this.config.entityPlaneColor,
            transparent: true,
            opacity: 0.08, // 稍微提高透明度，使平面更可见但不过于突出
            side: THREE.DoubleSide,
            depthWrite: false,  // 禁用深度写入，避免遮挡后面的连线
            depthTest: true     // 启用深度测试，但不会写入深度缓冲区
        });
        const entityPlane = new THREE.Mesh(entityPlaneGeometry, entityPlaneMaterial);
        // 平面默认在XY平面上，法向量指向Z轴正方向
        entityPlane.position.z = this.config.entityLayerZ;
        entityPlane.renderOrder = 0; // 较低的渲染顺序，先渲染
        this.scene.add(entityPlane);
        
        // 特征层平面
        const featurePlaneGeometry = new THREE.PlaneGeometry(350, 350);
        const featurePlaneMaterial = new THREE.MeshBasicMaterial({
            color: this.config.featurePlaneColor,
            transparent: true,
            opacity: 0.08, // 稍微提高透明度
            side: THREE.DoubleSide,
            depthWrite: false,  // 禁用深度写入，避免遮挡后面的连线
            depthTest: true     // 启用深度测试，但不会写入深度缓冲区
        });
        const featurePlane = new THREE.Mesh(featurePlaneGeometry, featurePlaneMaterial);
        featurePlane.position.z = this.config.featureLayerZ;
        featurePlane.renderOrder = 0; // 较低的渲染顺序，先渲染
        this.scene.add(featurePlane);
        
        // 场景层平面
        const scenePlaneGeometry = new THREE.PlaneGeometry(350, 350);
        const scenePlaneMaterial = new THREE.MeshBasicMaterial({
            color: this.config.scenePlaneColor,
            transparent: true,
            opacity: 0.08, // 稍微提高透明度
            side: THREE.DoubleSide,
            depthWrite: false,  // 禁用深度写入，避免遮挡后面的连线
            depthTest: true     // 启用深度测试，但不会写入深度缓冲区
        });
        const scenePlane = new THREE.Mesh(scenePlaneGeometry, scenePlaneMaterial);
        scenePlane.position.z = this.config.sceneLayerZ;
        scenePlane.renderOrder = 0; // 较低的渲染顺序，先渲染
        this.scene.add(scenePlane);
    }
    
    /**
     * 初始化性能监控
     */
    _initStats() {
        // 检查Stats是否可用
        if (typeof Stats !== 'undefined') {
            this.stats = new Stats();
            this.stats.showPanel(0); // 0: fps, 1: ms, 2: mb
            document.getElementById('stats-panel').appendChild(this.stats.dom);
        }
    }
    
    /**
     * 绑定事件
     */
    _bindEvents() {
        // 窗口大小变化
        window.addEventListener('resize', () => this.onWindowResize());
        
        // 点击事件
        this.renderer.domElement.addEventListener('click', (event) => this.onClick(event));
        
        // 双击事件
        this.renderer.domElement.addEventListener('dblclick', (event) => this.onDoubleClick(event));
    }
    
    /**
     * 窗口大小变化处理
     */
    onWindowResize() {
        const width = this.container.clientWidth;
        const height = this.container.clientHeight;
        
        this.camera.aspect = width / height;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(width, height);
    }
    
    /**
     * 点击事件处理
     */
    onClick(event) {
        // 实现射线检测选择节点
        const mouse = new THREE.Vector2();
        const rect = this.renderer.domElement.getBoundingClientRect();
        
        mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
        
        const raycaster = new THREE.Raycaster();
        raycaster.setFromCamera(mouse, this.camera);
        
        // 检查所有节点
        const intersects = raycaster.intersectObjects(Array.from(this.nodes.values()).map(node => node.mesh));
        
        if (intersects.length > 0) {
            const clickedMesh = intersects[0].object;
            
            // 防御性检查：确保userData存在
            if (!clickedMesh.userData) {
                console.error('点击的节点没有userData:', clickedMesh);
                return false;
            }
            
            const nodeId = clickedMesh.userData.nodeId;
            const nodeData = clickedMesh.userData.nodeData;
            
            // 防御性检查：确保nodeId存在
            if (!nodeId) {
                console.error('点击的节点没有nodeId:', clickedMesh.userData);
                return false;
            }
            
            // 触发节点点击事件
            this.trigger('nodeClick', {
                nodeId,
                nodeData: nodeData || { id: nodeId, label: '未知节点' },
                mesh: clickedMesh
            });
            
            // 高亮选中节点
            this.highlightNode(nodeId);
            
            return true;
        }
        
        return false;
    }
    
    /**
     * 双击事件处理
     */
    onDoubleClick(event) {
        if (this.onClick(event)) {
            // 如果点击到了节点，聚焦到该节点
            const mouse = new THREE.Vector2();
            const rect = this.renderer.domElement.getBoundingClientRect();
            
            mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
            mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
            
            const raycaster = new THREE.Raycaster();
            raycaster.setFromCamera(mouse, this.camera);
            
            const intersects = raycaster.intersectObjects(Array.from(this.nodes.values()).map(node => node.mesh));
            
            if (intersects.length > 0) {
                const clickedMesh = intersects[0].object;
                const nodeId = clickedMesh.userData.nodeId;
                
                // 聚焦到节点
                this.focusOnNode(nodeId);
            }
        }
    }
    
    /**
     * 渲染循环
     */
    animate() {
        requestAnimationFrame(() => this.animate());
        
        // 更新性能监控
        if (this.stats) {
            this.stats.update();
        }
        
        // 自动旋转 - 改为绕Z轴（深度轴）旋转，围绕知识图谱中心
        if (this.isRotating) {
            this.scene.rotation.z += this.config.rotationSpeed;
        }
        
        // 更新控制器
        if (this.controls) {
            this.controls.update();
        }
        
        // 更新摄像机信息显示
        this._updateCameraInfo();
        
        // 渲染场景
        if (this.renderer && this.scene && this.camera) {
            this.renderer.render(this.scene, this.camera);
        }
    }
    
    /**
     * 更新摄像机信息显示
     */
    _updateCameraInfo() {
        if (!this.camera || !this.controls) return;
        
        // 获取摄像机位置
        const cameraPos = this.camera.position;
        
        // 获取摄像机目标（控制器的焦点）
        const targetPos = this.controls.target;
        
        // 计算摄像机到目标的距离
        const distance = cameraPos.distanceTo(targetPos);
        
        // 计算球面坐标角度（θ为水平角，φ为垂直角）
        const direction = new THREE.Vector3().subVectors(cameraPos, targetPos);
        const radius = direction.length();
        
        // 水平角 θ (theta) - 在XY平面上的角度
        const theta = Math.atan2(direction.y, direction.x) * (180 / Math.PI);
        
        // 垂直角 φ (phi) - 与Z轴的夹角
        const phi = Math.acos(direction.z / radius) * (180 / Math.PI);
        
        // 获取摄像机视野
        const fov = this.camera.fov;
        
        // 更新UI显示
        this._updateCameraUI(cameraPos, targetPos, distance, theta, phi, fov);
    }
    
    /**
     * 更新摄像机UI显示
     */
    _updateCameraUI(cameraPos, targetPos, distance, theta, phi, fov) {
        // 格式化位置信息
        const formatVector = (vec) => {
            return `(${vec.x.toFixed(1)}, ${vec.y.toFixed(1)}, ${vec.z.toFixed(1)})`;
        };
        
        // 更新DOM元素
        const updateElement = (id, text) => {
            const element = document.getElementById(id);
            if (element) {
                element.textContent = text;
            }
        };
        
        updateElement('camera-position', formatVector(cameraPos));
        updateElement('camera-target', formatVector(targetPos));
        updateElement('camera-distance', distance.toFixed(1));
        updateElement('camera-angles', `(${theta.toFixed(1)}°, ${phi.toFixed(1)}°)`);
        updateElement('camera-fov', `${fov.toFixed(1)}°`);
    }
    
    /**
     * 设置摄像机位置（使用球面坐标）
     * @param {number} theta - 水平角（度）
     * @param {number} phi - 垂直角（度）
     * @param {number} distance - 距离
     * @param {THREE.Vector3} target - 目标点（可选，默认为当前目标）
     */
    setCameraPosition(theta, phi, distance, target = null) {
        if (!this.camera || !this.controls) return;
        
        // 将角度转换为弧度
        const thetaRad = theta * (Math.PI / 180);
        const phiRad = phi * (Math.PI / 180);
        
        // 使用当前目标或默认目标
        const targetPos = target || this.controls.target.clone();
        
        // 计算球面坐标
        const x = distance * Math.sin(phiRad) * Math.cos(thetaRad);
        const y = distance * Math.sin(phiRad) * Math.sin(thetaRad);
        const z = distance * Math.cos(phiRad);
        
        // 设置摄像机位置（相对于目标点）
        this.camera.position.set(
            targetPos.x + x,
            targetPos.y + y,
            targetPos.z + z
        );
        
        // 更新控制器目标
        this.controls.target.copy(targetPos);
        
        // 更新控制器
        this.controls.update();
        
        console.log(`摄像机位置已设置: θ=${theta}°, φ=${phi}°, 距离=${distance}`);
    }
    
    /**
     * 重置摄像机到默认位置
     */
    resetCamera() {
        if (!this.camera || !this.controls) return;
        
        // 默认摄像机位置
        this.camera.position.set(200, 200, 300);
        this.controls.target.set(0, 0, 100);
        this.controls.update();
        
        console.log('摄像机已重置到默认位置');
    }
    
    /**
     * 加载图数据
     */
    loadGraphData(graphData) {
        if (!this.isInitialized) {
            console.error('场景未初始化');
            return;
        }
        
        // 清空现有节点和边
        this.clearScene();
        
        // 创建节点布局
        const layout = this._createNodeLayout(graphData);
        
        // 创建节点
        this._createNodes(graphData, layout);
        
        // 创建边
        this._createEdges(graphData, layout);
        
        console.log(`加载了 ${this.nodes.size} 个节点和 ${this.edges.size} 条边`);
    }
    
    /**
     * 创建节点布局（改进版，确保垂直边正确连接）
     */
    _createNodeLayout(graphData) {
        const layout = {};
        
        // 按层分组
        const entities = graphData.entities;
        const features = graphData.features;
        const scenes = graphData.scenes;
        
        // 实体层布局（圆形排列）
        const entityRadius = Math.max(60, Math.sqrt(entities.length) * 20);
        const entityAngles = {};
        
        entities.forEach((entity, index) => {
            const angle = (index / entities.length) * Math.PI * 2;
            entityAngles[entity.id] = angle;
            layout[entity.id] = {
                x: Math.cos(angle) * entityRadius,
                y: Math.sin(angle) * entityRadius,  // Y轴作为水平方向
                z: this.config.entityLayerZ         // Z轴作为垂直轴
            };
        });
        
        // 特征层布局（直接对应实体位置，确保垂直边对齐）
        features.forEach((feature, index) => {
            const entity = entities.find(e => e.id === feature.entity_id);
            if (entity && layout[entity.id]) {
                // 获取该实体的所有特征
                const entityFeatures = features.filter(f => f.entity_id === feature.entity_id);
                const featureIndex = entityFeatures.indexOf(feature);
                const totalFeatures = entityFeatures.length;
                
                if (totalFeatures === 1) {
                    // 单个特征：直接对齐实体
                    layout[feature.id] = {
                        x: layout[entity.id].x,
                        y: layout[entity.id].y,
                        z: this.config.featureLayerZ  // Z轴作为垂直轴
                    };
                } else {
                    // 多个特征：在实体周围均匀分布
                    const featureRadius = 35;
                    const featureAngle = entityAngles[entity.id] + (featureIndex / totalFeatures) * Math.PI * 2;
                    layout[feature.id] = {
                        x: layout[entity.id].x + Math.cos(featureAngle) * featureRadius,
                        y: layout[entity.id].y + Math.sin(featureAngle) * featureRadius,
                        z: this.config.featureLayerZ  // Z轴作为垂直轴
                    };
                }
            } else {
                // 备用布局
                layout[feature.id] = {
                    x: (index % 8) * 40 - 140,
                    y: Math.floor(index / 8) * 40 - 140,
                    z: this.config.featureLayerZ  // Z轴作为垂直轴
                };
            }
        });
        
        // 场景层布局（直接对应特征位置，确保垂直边对齐）
        scenes.forEach((scene, index) => {
            // 查找相关的特征
            const relatedFeatures = features.filter(f =>
                graphData.vertical_edges.some(e =>
                    e.from === f.id && e.to === scene.id && e.type === 'feature_scene'
                )
            );
            
            if (relatedFeatures.length > 0) {
                // 如果有多个相关特征，取第一个
                const feature = relatedFeatures[0];
                if (layout[feature.id]) {
                    // 获取该特征的所有场景
                    const featureScenes = scenes.filter(s =>
                        graphData.vertical_edges.some(e =>
                            e.from === feature.id && e.to === s.id && e.type === 'feature_scene'
                        )
                    );
                    const sceneIndex = featureScenes.indexOf(scene);
                    const totalScenes = featureScenes.length;
                    
                    if (totalScenes === 1) {
                        // 单个场景：直接对齐特征
                        layout[scene.id] = {
                            x: layout[feature.id].x,
                            y: layout[feature.id].y,
                            z: this.config.sceneLayerZ  // Z轴作为垂直轴
                        };
                    } else {
                        // 多个场景：在特征周围均匀分布
                        const sceneRadius = 25;
                        const sceneAngle = (sceneIndex / totalScenes) * Math.PI * 2;
                        layout[scene.id] = {
                            x: layout[feature.id].x + Math.cos(sceneAngle) * sceneRadius,
                            y: layout[feature.id].y + Math.sin(sceneAngle) * sceneRadius,
                            z: this.config.sceneLayerZ  // Z轴作为垂直轴
                        };
                    }
                } else {
                    // 特征位置未知
                    layout[scene.id] = {
                        x: (index % 10) * 30 - 135,
                        y: Math.floor(index / 10) * 30 - 135,
                        z: this.config.sceneLayerZ  // Z轴作为垂直轴
                    };
                }
            } else {
                // 没有相关特征：独立场景
                layout[scene.id] = {
                    x: (index % 10) * 30 - 135,
                    y: Math.floor(index / 10) * 30 - 135,
                    z: this.config.sceneLayerZ  // Z轴作为垂直轴
                };
            }
        });
        
        return layout;
    }
    
    /**
     * 创建节点
     */
    _createNodes(graphData, layout) {
        // 创建实体节点
        graphData.entities.forEach(entity => {
            this._createEntityNode(entity, layout[entity.id]);
        });
        
        // 创建特征节点
        graphData.features.forEach(feature => {
            this._createFeatureNode(feature, layout[feature.id]);
        });
        
        // 创建场景节点
        graphData.scenes.forEach(scene => {
            this._createSceneNode(scene, layout[scene.id]);
        });
    }
    
    /**
     * 创建实体节点
     */
    _createEntityNode(entity, position) {
        // 防御性检查：确保entity对象存在且有必要的属性
        if (!entity) {
            console.error('创建实体节点失败：entity对象为空');
            return null;
        }
        
        // 确保entity有必要的属性
        const safeEntity = {
            id: entity.id || `entity_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
            label: entity.label || entity.id || '未知实体',
            color: entity.color || this.config.entityColor,
            ...entity // 保留其他属性
        };
        
        // 创建球体几何体
        const geometry = new THREE.SphereGeometry(this.config.entityNodeSize, 16, 16);
        
        // 创建材质 - 使用更亮的颜色
        const color = new THREE.Color(safeEntity.color);
        const material = new THREE.MeshPhongMaterial({
            color: color,
            shininess: 50, // 增加光泽度，使节点更亮
            transparent: true,
            opacity: 0.95, // 提高不透明度
            emissive: new THREE.Color(0x111111), // 添加微弱的自发光
            emissiveIntensity: 0.1
        });
        
        // 创建网格
        const mesh = new THREE.Mesh(geometry, material);
        mesh.position.set(position.x, position.y, position.z);
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        
        // 保存节点数据 - 使用安全的entity对象
        mesh.userData = {
            nodeId: safeEntity.id,
            nodeData: safeEntity,
            nodeType: 'entity'
        };
        
        // 添加到实体层
        this.entityLayer.add(mesh);
        
        // 创建标签（调整标签位置，使其在节点上方）
        const labelPosition = {
            x: position.x,
            y: position.y,
            z: position.z + 20 // 标签在节点上方（Z轴方向）
        };
        const label = this._createNodeLabel(safeEntity.label, labelPosition);
        this.entityLayer.add(label);
        
        // 保存到节点集合
        this.nodes.set(safeEntity.id, {
            mesh: mesh,
            label: label,
            data: safeEntity,
            layer: 'entity'
        });
        
        console.log(`创建实体节点: ${safeEntity.id} (${safeEntity.label})`);
        return mesh;
    }
    
    /**
     * 创建特征节点
     */
    _createFeatureNode(feature, position) {
        // 防御性检查：确保feature对象存在且有必要的属性
        if (!feature) {
            console.error('创建特征节点失败：feature对象为空');
            return null;
        }
        
        // 确保feature有必要的属性
        const safeFeature = {
            id: feature.id || `feature_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
            label: feature.label || feature.id || '未知特征',
            color: feature.color || this.config.featureColor,
            entity_id: feature.entity_id || null,
            ...feature // 保留其他属性
        };
        
        // 创建立方体几何体
        const geometry = new THREE.BoxGeometry(
            this.config.featureNodeSize,
            this.config.featureNodeSize,
            this.config.featureNodeSize
        );
        
        // 创建材质 - 使用更亮的颜色
        const color = new THREE.Color(safeFeature.color);
        const material = new THREE.MeshPhongMaterial({
            color: color,
            shininess: 40, // 增加光泽度
            transparent: true,
            opacity: 0.9, // 提高不透明度
            emissive: new THREE.Color(0x111111), // 添加微弱的自发光
            emissiveIntensity: 0.1
        });
        
        // 创建网格
        const mesh = new THREE.Mesh(geometry, material);
        mesh.position.set(position.x, position.y, position.z);
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        
        // 保存节点数据 - 使用安全的feature对象
        mesh.userData = {
            nodeId: safeFeature.id,
            nodeData: safeFeature,
            nodeType: 'feature'
        };
        
        // 添加到特征层
        this.featureLayer.add(mesh);
        
        // 创建标签
        const label = this._createNodeLabel(safeFeature.label, position);
        this.featureLayer.add(label);
        
        // 保存到节点集合
        this.nodes.set(safeFeature.id, {
            mesh: mesh,
            label: label,
            data: safeFeature,
            layer: 'feature'
        });
        
        console.log(`创建特征节点: ${safeFeature.id} (${safeFeature.label})`);
        return mesh;
    }
    
    /**
     * 创建场景节点
     */
    _createSceneNode(scene, position) {
        // 防御性检查：确保scene对象存在且有必要的属性
        if (!scene) {
            console.error('创建场景节点失败：scene对象为空');
            return null;
        }
        
        // 确保scene有必要的属性
        const safeScene = {
            id: scene.id || `scene_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
            label: scene.label || scene.id || '未知场景',
            color: scene.color || this.config.sceneColor,
            ...scene // 保留其他属性
        };
        
        // 创建四面体几何体
        const geometry = new THREE.ConeGeometry(this.config.sceneNodeSize, this.config.sceneNodeSize * 2, 4);
        
        // 创建材质 - 使用更亮的颜色
        const color = new THREE.Color(safeScene.color);
        const material = new THREE.MeshPhongMaterial({
            color: color,
            shininess: 30, // 增加光泽度
            transparent: true,
            opacity: 0.85, // 提高不透明度
            emissive: new THREE.Color(0x111111), // 添加微弱的自发光
            emissiveIntensity: 0.1
        });
        
        // 创建网格
        const mesh = new THREE.Mesh(geometry, material);
        mesh.position.set(position.x, position.y, position.z);
        // ConeGeometry默认指向Y轴正方向，我们需要它指向Z轴正方向（垂直轴）
        mesh.rotation.x = -Math.PI / 2; // 旋转使锥体指向Z轴
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        
        // 保存节点数据 - 使用安全的scene对象
        mesh.userData = {
            nodeId: safeScene.id,
            nodeData: safeScene,
            nodeType: 'scene'
        };
        
        // 添加到场景层
        this.sceneLayer.add(mesh);
        
        // 创建标签
        const label = this._createNodeLabel(safeScene.label, position);
        this.sceneLayer.add(label);
        
        // 保存到节点集合
        this.nodes.set(safeScene.id, {
            mesh: mesh,
            label: label,
            data: safeScene,
            layer: 'scene'
        });
        
        console.log(`创建场景节点: ${safeScene.id} (${safeScene.label})`);
        return mesh;
    }
    
    /**
     * 创建节点标签
     */
    _createNodeLabel(text, position) {
        // 创建Canvas用于渲染文本
        const canvas = document.createElement('canvas');
        const context = canvas.getContext('2d');
        canvas.width = 256;
        canvas.height = 128;
        
        // 绘制文本
        context.fillStyle = 'rgba(255, 255, 255, 0.8)';
        context.font = '24px Arial';
        context.textAlign = 'center';
        context.textBaseline = 'middle';
        context.fillText(text, canvas.width / 2, canvas.height / 2);
        
        // 创建纹理
        const texture = new THREE.CanvasTexture(canvas);
        texture.minFilter = THREE.LinearFilter;
        
        // 创建精灵材质
        const spriteMaterial = new THREE.SpriteMaterial({
            map: texture,
            transparent: true,
            opacity: 0.8
        });
        
        // 创建精灵
        const sprite = new THREE.Sprite(spriteMaterial);
        sprite.position.set(position.x, position.y, position.z);
        sprite.scale.set(30, 15, 1);
        
        return sprite;
    }
    
    /**
     * 创建边
     */
    _createEdges(graphData, layout) {
        // 创建水平边（实体间关系）
        graphData.horizontal_edges.forEach(edge => {
            this._createHorizontalEdge(edge, layout);
        });
        
        // 创建垂直边（实体-特征，特征-场景）
        graphData.vertical_edges.forEach(edge => {
            this._createVerticalEdge(edge, layout);
        });
    }
    
    /**
     * 创建水平边
     */
    _createHorizontalEdge(edge, layout) {
        const fromPos = layout[edge.from];
        const toPos = layout[edge.to];
        
        if (!fromPos || !toPos) {
            console.warn(`无法创建边 ${edge.from} -> ${edge.to}，位置信息缺失`);
            return;
        }
        
        // 创建曲线
        const curve = new THREE.CatmullRomCurve3([
            new THREE.Vector3(fromPos.x, fromPos.y, fromPos.z),
            new THREE.Vector3(
                (fromPos.x + toPos.x) / 2,
                (fromPos.y + toPos.y) / 2,
                fromPos.z + 20
            ),
            new THREE.Vector3(toPos.x, toPos.y, toPos.z)
        ]);
        
        // 创建线几何体
        const points = curve.getPoints(50);
        const geometry = new THREE.BufferGeometry().setFromPoints(points);
        
        // 创建材质
        const color = new THREE.Color(edge.color || '#7f8c8d');
        const material = new THREE.LineBasicMaterial({
            color: color,
            transparent: true,
            opacity: 0.6,
            linewidth: 2,
            depthTest: true,    // 启用深度测试
            depthWrite: true    // 启用深度写入
        });
        
        // 创建线
        const line = new THREE.Line(geometry, material);
        line.renderOrder = 1; // 较高的渲染顺序，后渲染（在平面之后）
        this.scene.add(line);
        
        // 保存边数据
        this.edges.set(edge.id, {
            line: line,
            data: edge,
            type: 'horizontal'
        });
    }
    
    /**
     * 创建垂直边
     */
    _createVerticalEdge(edge, layout) {
        const fromPos = layout[edge.from];
        const toPos = layout[edge.to];
        
        if (!fromPos || !toPos) {
            console.warn(`无法创建垂直边 ${edge.from} -> ${edge.to}，位置信息缺失`);
            return;
        }
        
        // 创建直线（垂直连接）
        const geometry = new THREE.BufferGeometry().setFromPoints([
            new THREE.Vector3(fromPos.x, fromPos.y, fromPos.z),
            new THREE.Vector3(toPos.x, toPos.y, toPos.z)
        ]);
        
        // 创建材质（虚线）
        const color = new THREE.Color(edge.color || '#3498db');
        const material = new THREE.LineDashedMaterial({
            color: color,
            transparent: true,
            opacity: 0.4,
            dashSize: 3,
            gapSize: 2,
            linewidth: 1,
            depthTest: true,    // 启用深度测试
            depthWrite: true    // 启用深度写入
        });
        
        // 创建线
        const line = new THREE.Line(geometry, material);
        line.computeLineDistances();
        line.renderOrder = 1; // 较高的渲染顺序，后渲染（在平面之后）
        this.scene.add(line);
        
        // 保存边数据
        this.edges.set(edge.id, {
            line: line,
            data: edge,
            type: 'vertical'
        });
    }
    
    /**
     * 清空场景
     */
    clearScene() {
        // 移除所有节点
        this.nodes.forEach(node => {
            if (node.mesh && node.mesh.parent) {
                node.mesh.parent.remove(node.mesh);
            }
            if (node.label && node.label.parent) {
                node.label.parent.remove(node.label);
            }
        });
        this.nodes.clear();
        
        // 移除所有边
        this.edges.forEach(edge => {
            if (edge.line && edge.line.parent) {
                edge.line.parent.remove(edge.line);
            }
        });
        this.edges.clear();
        
        console.log('场景已清空');
    }
    
    /**
     * 高亮节点
     */
    highlightNode(nodeId) {
        // 重置所有节点高亮
        this.resetHighlights();
        
        const node = this.nodes.get(nodeId);
        if (!node) return;
        
        // 高亮节点
        node.mesh.material.emissive = new THREE.Color(0xffff00);
        node.mesh.material.emissiveIntensity = 0.5;
        node.mesh.material.needsUpdate = true;
        
        // 高亮标签
        if (node.label && node.label.material) {
            node.label.material.opacity = 1.0;
            node.label.material.needsUpdate = true;
        }
        
        // 高亮相关边
        this._highlightRelatedEdges(nodeId);
    }
    
    /**
     * 高亮相关边
     */
    _highlightRelatedEdges(nodeId) {
        this.edges.forEach((edge, edgeId) => {
            if (edge.data.from === nodeId || edge.data.to === nodeId) {
                edge.line.material.color = new THREE.Color(0xffff00);
                edge.line.material.opacity = 1.0;
                edge.line.material.needsUpdate = true;
            }
        });
    }
    
    /**
     * 重置高亮
     */
    resetHighlights() {
        // 重置节点
        this.nodes.forEach(node => {
            if (node.mesh && node.mesh.material) {
                node.mesh.material.emissive = new THREE.Color(0x000000);
                node.mesh.material.emissiveIntensity = 0;
                node.mesh.material.needsUpdate = true;
            }
            
            if (node.label && node.label.material) {
                node.label.material.opacity = 0.8;
                node.label.material.needsUpdate = true;
            }
        });
        
        // 重置边
        this.edges.forEach(edge => {
            if (edge.line && edge.line.material) {
                const originalColor = edge.data.color || '#7f8c8d';
                edge.line.material.color = new THREE.Color(originalColor);
                edge.line.material.opacity = edge.type === 'horizontal' ? 0.6 : 0.4;
                edge.line.material.needsUpdate = true;
            }
        });
    }
    
    /**
     * 聚焦到节点
     */
    focusOnNode(nodeId) {
        const node = this.nodes.get(nodeId);
        if (!node) return;
        
        const position = node.mesh.position.clone();
        
        // 移动相机到节点位置（从斜上方观察）
        const targetPosition = position.clone();
        targetPosition.x += 100;
        targetPosition.y += 100;
        targetPosition.z += 150; // 从上方观察（Z轴方向）
        
        // 动画过渡
        this._animateCameraTo(targetPosition, position);
    }
    
    /**
     * 动画移动相机
     */
    _animateCameraTo(cameraPosition, lookAtPosition) {
        const startPosition = this.camera.position.clone();
        const startLookAt = new THREE.Vector3();
        this.controls.target.clone(startLookAt);
        
        const duration = 1000; // 1秒
        const startTime = Date.now();
        
        const animate = () => {
            const elapsed = Date.now() - startTime;
            const progress = Math.min(elapsed / duration, 1);
            
            // 缓动函数
            const ease = this._easeOutCubic(progress);
            
            // 插值位置
            this.camera.position.lerpVectors(startPosition, cameraPosition, ease);
            
            // 插值目标
            const currentLookAt = new THREE.Vector3();
            currentLookAt.lerpVectors(startLookAt, lookAtPosition, ease);
            this.controls.target.copy(currentLookAt);
            this.controls.update();
            
            if (progress < 1) {
                requestAnimationFrame(animate);
            }
        };
        
        animate();
    }
    
    /**
     * 缓动函数
     */
    _easeOutCubic(t) {
        return 1 - Math.pow(1 - t, 3);
    }
    
    /**
     * 切换自动旋转
     */
    toggleRotation() {
        this.isRotating = !this.isRotating;
        return this.isRotating;
    }
    
    /**
     * 切换网格显示
     */
    toggleGrid() {
        this.showGrid = !this.showGrid;
        
        if (this.gridHelper) {
            this.gridHelper.visible = this.showGrid;
        }
        
        return this.showGrid;
    }
    
    /**
     * 切换坐标轴显示
     */
    toggleAxes() {
        this.showAxes = !this.showAxes;
        
        if (this.axesHelper) {
            this.axesHelper.visible = this.showAxes;
        }
        
        return this.showAxes;
    }
    
    /**
     * 切换层显示
     */
    toggleLayer(layerName, visible) {
        let layer = null;
        
        switch (layerName) {
            case 'entity':
                layer = this.entityLayer;
                break;
            case 'feature':
                layer = this.featureLayer;
                break;
            case 'scene':
                layer = this.sceneLayer;
                break;
        }
        
        if (layer) {
            layer.visible = visible;
            return true;
        }
        
        return false;
    }
    
    /**
     * 切换边显示
     */
    toggleEdges(edgeType, visible) {
        let changed = false;
        
        this.edges.forEach((edge, edgeId) => {
            if (edge.type === edgeType && edge.line) {
                edge.line.visible = visible;
                changed = true;
            }
        });
        
        return changed;
    }
    
    /**
     * 导出截图
     */
    exportScreenshot() {
        const link = document.createElement('a');
        link.download = `knowledge-graph-3d-${new Date().toISOString().slice(0, 10)}.png`;
        link.href = this.renderer.domElement.toDataURL('image/png');
        link.click();
        
        return true;
    }
    
    /**
     * 事件触发器
     */
    trigger(eventName, data) {
        // 创建自定义事件
        const event = new CustomEvent(`threeScene:${eventName}`, { detail: data });
        document.dispatchEvent(event);
    }
    
    /**
     * 销毁场景
     */
    destroy() {
        // 停止动画循环
        this.isInitialized = false;
        
        // 移除事件监听
        window.removeEventListener('resize', () => this.onWindowResize());
        
        // 清空场景
        this.clearScene();
        
        // 移除渲染器
        if (this.renderer && this.renderer.domElement.parentNode) {
            this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
        }
        
        // 清理Three.js对象
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.controls = null;
        
        console.log('Three.js场景已销毁');
    }
}

// 导出类
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ThreeSceneManager;
}