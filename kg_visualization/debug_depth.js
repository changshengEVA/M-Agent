/**
 * 三维可视化深度调试工具
 * 用于检查各个平面和节点的深度信息
 */

class DepthDebugger {
    constructor(sceneManager) {
        this.sceneManager = sceneManager;
        this.debugInfo = {
            planes: {},
            nodes: {},
            layers: {}
        };
    }

    /**
     * 收集深度信息
     */
    collectDepthInfo() {
        this._collectPlaneDepths();
        this._collectLayerDepths();
        this._collectNodeDepths();
        return this.debugInfo;
    }

    /**
     * 收集平面深度信息
     */
    _collectPlaneDepths() {
        const scene = this.sceneManager.scene;
        if (!scene) return;

        // 查找所有平面
        scene.traverse((object) => {
            if (object.isMesh && object.geometry && object.geometry.type === 'PlaneGeometry') {
                const planeInfo = {
                    name: object.name || '未命名平面',
                    position: {
                        x: object.position.x.toFixed(2),
                        y: object.position.y.toFixed(2),
                        z: object.position.z.toFixed(2)
                    },
                    rotation: {
                        x: (object.rotation.x * 180 / Math.PI).toFixed(2) + '°',
                        y: (object.rotation.y * 180 / Math.PI).toFixed(2) + '°',
                        z: (object.rotation.z * 180 / Math.PI).toFixed(2) + '°'
                    },
                    normal: this._calculateNormal(object),
                    materialColor: object.material.color ? 
                        `#${object.material.color.getHexString()}` : '未知'
                };
                
                this.debugInfo.planes[object.uuid] = planeInfo;
            }
        });
    }

    /**
     * 计算平面法向量
     */
    _calculateNormal(object) {
        // 平面的默认法向量是 (0, 0, 1)
        const defaultNormal = new THREE.Vector3(0, 0, 1);
        
        // 应用旋转
        const normal = defaultNormal.clone();
        normal.applyEuler(object.rotation);
        
        return {
            x: normal.x.toFixed(3),
            y: normal.y.toFixed(3),
            z: normal.z.toFixed(3),
            direction: this._getDirectionDescription(normal)
        };
    }

    /**
     * 获取方向描述
     */
    _getDirectionDescription(normal) {
        const absX = Math.abs(normal.x);
        const absY = Math.abs(normal.y);
        const absZ = Math.abs(normal.z);
        
        if (absZ > absX && absZ > absY) {
            return normal.z > 0 ? '指向Z轴正方向（前/上）' : '指向Z轴负方向（后/下）';
        } else if (absY > absX && absY > absZ) {
            return normal.y > 0 ? '指向Y轴正方向（上）' : '指向Y轴负方向（下）';
        } else {
            return normal.x > 0 ? '指向X轴正方向（右）' : '指向X轴负方向（左）';
        }
    }

    /**
     * 收集层深度信息
     */
    _collectLayerDepths() {
        const config = this.sceneManager.config;
        
        this.debugInfo.layers = {
            entity: {
                name: '实体层',
                depth: config.entityLayerZ,
                description: `Z = ${config.entityLayerZ}`
            },
            feature: {
                name: '特征层',
                depth: config.featureLayerZ,
                description: `Z = ${config.featureLayerZ}`,
                offsetFromEntity: config.featureLayerZ - config.entityLayerZ
            },
            scene: {
                name: '场景层',
                depth: config.sceneLayerZ,
                description: `Z = ${config.sceneLayerZ}`,
                offsetFromFeature: config.sceneLayerZ - config.featureLayerZ
            }
        };
    }

    /**
     * 收集节点深度信息
     */
    _collectNodeDepths() {
        const nodes = this.sceneManager.nodes;
        if (!nodes || nodes.size === 0) return;

        let minZ = Infinity;
        let maxZ = -Infinity;
        const layerStats = {
            entity: { count: 0, minZ: Infinity, maxZ: -Infinity, avgZ: 0 },
            feature: { count: 0, minZ: Infinity, maxZ: -Infinity, avgZ: 0 },
            scene: { count: 0, minZ: Infinity, maxZ: -Infinity, avgZ: 0 }
        };

        nodes.forEach((node, nodeId) => {
            const position = node.mesh.position;
            const layer = node.layer;
            
            // 更新全局统计
            minZ = Math.min(minZ, position.z);
            maxZ = Math.max(maxZ, position.z);
            
            // 更新层统计
            if (layerStats[layer]) {
                layerStats[layer].count++;
                layerStats[layer].minZ = Math.min(layerStats[layer].minZ, position.z);
                layerStats[layer].maxZ = Math.max(layerStats[layer].maxZ, position.z);
                layerStats[layer].avgZ += position.z;
            }

            // 记录节点信息
            this.debugInfo.nodes[nodeId] = {
                id: nodeId,
                type: node.data.type || node.layer,
                layer: node.layer,
                position: {
                    x: position.x.toFixed(2),
                    y: position.y.toFixed(2),
                    z: position.z.toFixed(2)
                },
                label: node.data.label || nodeId
            };
        });

        // 计算平均值
        Object.keys(layerStats).forEach(layer => {
            if (layerStats[layer].count > 0) {
                layerStats[layer].avgZ /= layerStats[layer].count;
                layerStats[layer].avgZ = layerStats[layer].avgZ.toFixed(2);
                layerStats[layer].minZ = layerStats[layer].minZ.toFixed(2);
                layerStats[layer].maxZ = layerStats[layer].maxZ.toFixed(2);
            }
        });

        this.debugInfo.nodeStatistics = {
            totalNodes: nodes.size,
            globalDepthRange: {
                min: minZ.toFixed(2),
                max: maxZ.toFixed(2),
                range: (maxZ - minZ).toFixed(2)
            },
            layerStats: layerStats
        };
    }

    /**
     * 生成深度报告
     */
    generateReport() {
        const info = this.debugInfo;
        let report = '=== 三维可视化深度调试报告 ===\n\n';

        // 层深度信息
        report += '## 1. 层深度配置\n';
        Object.values(info.layers).forEach(layer => {
            report += `- ${layer.name}: ${layer.description}\n`;
            if (layer.offsetFromEntity !== undefined) {
                report += `  距离实体层: ${layer.offsetFromEntity} 单位\n`;
            }
            if (layer.offsetFromFeature !== undefined) {
                report += `  距离特征层: ${layer.offsetFromFeature} 单位\n`;
            }
        });

        // 平面深度信息
        if (Object.keys(info.planes).length > 0) {
            report += '\n## 2. 平面深度信息\n';
            Object.values(info.planes).forEach((plane, index) => {
                report += `平面 ${index + 1} (${plane.name}):\n`;
                report += `  位置: (${plane.position.x}, ${plane.position.y}, ${plane.position.z})\n`;
                report += `  法向量: (${plane.normal.x}, ${plane.normal.y}, ${plane.normal.z})\n`;
                report += `  方向: ${plane.normal.direction}\n`;
                report += `  颜色: ${plane.materialColor}\n`;
            });
        }

        // 节点深度统计
        if (info.nodeStatistics) {
            report += '\n## 3. 节点深度统计\n';
            report += `总节点数: ${info.nodeStatistics.totalNodes}\n`;
            report += `全局深度范围: ${info.nodeStatistics.globalDepthRange.min} 到 ${info.nodeStatistics.globalDepthRange.max} (范围: ${info.nodeStatistics.globalDepthRange.range})\n\n`;

            report += '各层节点深度分布:\n';
            Object.entries(info.nodeStatistics.layerStats).forEach(([layer, stats]) => {
                if (stats.count > 0) {
                    report += `- ${layer}层: ${stats.count} 个节点\n`;
                    report += `  最小深度: ${stats.minZ}, 最大深度: ${stats.maxZ}, 平均深度: ${stats.avgZ}\n`;
                }
            });
        }

        // 节点详细信息（前10个）
        if (Object.keys(info.nodes).length > 0) {
            report += '\n## 4. 节点深度示例（前10个）\n';
            const nodeEntries = Object.entries(info.nodes);
            for (let i = 0; i < Math.min(10, nodeEntries.length); i++) {
                const [nodeId, node] = nodeEntries[i];
                report += `${i + 1}. ${node.label} (${node.layer}层): Z = ${node.position.z}\n`;
            }
        }

        return report;
    }

    /**
     * 在控制台输出报告
     */
    logReport() {
        const report = this.generateReport();
        console.log(report);
        
        // 同时输出到页面
        this._displayReport(report);
    }

    /**
     * 在页面上显示报告
     */
    _displayReport(report) {
        // 创建或获取调试面板
        let debugPanel = document.getElementById('depth-debug-panel');
        if (!debugPanel) {
            debugPanel = document.createElement('div');
            debugPanel.id = 'depth-debug-panel';
            debugPanel.style.cssText = `
                position: fixed;
                top: 10px;
                right: 10px;
                width: 400px;
                max-height: 500px;
                background: rgba(0, 0, 0, 0.8);
                color: white;
                padding: 10px;
                font-family: monospace;
                font-size: 12px;
                overflow-y: auto;
                z-index: 1000;
                border: 1px solid #444;
                border-radius: 5px;
            `;
            document.body.appendChild(debugPanel);
        }

        debugPanel.innerHTML = `<pre style="margin: 0; color: #fff;">${report}</pre>`;
    }
}

// 导出
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DepthDebugger;
}