console.log("PowerTwin Solver UI v2.0 loaded");

// ============= Global Variables =============
let simulationPerformanceCharts = {
    performance: null,
    throughput: null,
    successRate: null
};

let simulationPerformanceData = {
    timestamps: [],
    avgSimulationTime: [],
    completionPercentage: [],
    throughput: [],
    successRate: []
};

let currentTimeRange = null;  // null = All
let simulationPollingInterval = null;
const MAX_HISTORY_POINTS = 1000;

// ============= Initialization =============
document.addEventListener('DOMContentLoaded', function() {
    console.log("DOM loaded, initializing...");
    loadSystemTypes();
    loadBuildingTypes();
    initializeDashboard();
    initializePerformanceMonitoring();
    loadLogs();
    startPeriodicUpdates();
});

// ============= Tab Switching =============
function switchTab(tabName) {
    console.log(`Switching to tab: ${tabName}`);
    
    document.querySelectorAll('[id$="-tab"]').forEach(tab => {
        tab.classList.remove('tab-content-active');
        tab.classList.add('tab-content');
    });
    
    const selectedTab = document.getElementById(tabName + '-tab');
    if (selectedTab) {
        selectedTab.classList.remove('tab-content');
        selectedTab.classList.add('tab-content-active');
    }
    
    document.querySelectorAll('.sidebar-link').forEach(link => {
        link.classList.remove('active');
    });
    
    const activeLink = document.querySelector(`.sidebar-link[onclick*="${tabName}"]`);
    if (activeLink) {
        activeLink.classList.add('active');
    }
    
    const titles = {
        'dashboard': 'Dashboard',
        'simulation': 'Simulation Control',
        'performance': 'Performance Metrics',
        'logs': 'Application Logs',
        'assets': 'Asset Configuration'
    };
    document.getElementById('page-title').textContent = titles[tabName] || 'Dashboard';
    
    if (tabName === 'performance' && !simulationPerformanceCharts.performance) {
        setTimeout(() => {
            initializeSimulationPerformanceCharts();
        }, 100);
    }
}

// ============= Configuration Loading =============
function loadSystemTypes() {
    fetch('/static/json/system_types.json')
        .then(response => response.json())
        .then(data => {
            const systemTypeSelects = document.querySelectorAll('.system_type');
            systemTypeSelects.forEach(select => {
                data.system_types.forEach(type => {
                    const option = document.createElement('option');
                    option.value = type;
                    option.textContent = type;
                    select.appendChild(option);
                });
            });
        })
        .catch(error => console.error('Error loading system types:', error));
}

function loadBuildingTypes() {
    fetch('/static/json/building_types.json')
        .then(response => response.json())
        .then(data => {
            const buildingTypeSelects = document.querySelectorAll('.building_type');
            buildingTypeSelects.forEach(select => {
                data.building_types.forEach(type => {
                    const option = document.createElement('option');
                    option.value = type;
                    option.textContent = type;
                    select.appendChild(option);
                });
            });
        })
        .catch(error => console.error('Error loading building types:', error));
}

// ============= Form Toggles =============
function toggleConfigurationSettings() {
    const advancedSettings = document.getElementById('configuration-settings');
    const checkbox = document.getElementById('show-configuration-settings');
    advancedSettings.style.display = checkbox.checked ? 'block' : 'none';
}

function toggleHpcSettings() {
    const hpcSettings = document.getElementById('hpc-settings');
    const checkbox = document.getElementById('startsim_hpc_mode');
    hpcSettings.style.display = checkbox.checked ? 'block' : 'none';
}

// ============= Dashboard =============
function initializeDashboard() {
    fetchSystemStatus();
    fetchCurrentSimulationStatus();
    fetchBatchProgress();
    setInterval(fetchSystemStatus, 60000);
    setInterval(fetchCurrentSimulationStatus, 60000);
    setInterval(fetchBatchProgress, 60000);  // Polling for batch progress
}

// ============= Performance Monitoring =============
function initializePerformanceMonitoring() {
    fetchSimulationPerformanceMetrics();
    setInterval(fetchSimulationPerformanceMetrics, 60000);  // Poll every 60 seconds
    console.log('Started continuous performance monitoring (60s interval)');
}

function fetchCurrentSimulationStatus() {
    fetch('/api/simulation/current-status')
        .then(response => response.json())
        .then(data => updateSimulationProgressDisplay(data))
        .catch(error => console.error('Error fetching simulation status:', error));
}

function updateSimulationProgressDisplay(data) {
    const simProgressContent = document.getElementById('sim-progress-content');
    
    if (!data.has_active_simulation) {
        simProgressContent.innerHTML = '<p class="text-muted">No active simulation</p>';
        updateStatusIndicator('Idle');
        return;
    }
    
    // Update status indicator based on actual simulation status
    const statusDisplay = data.status.charAt(0).toUpperCase() + data.status.slice(1);
    updateStatusIndicator(statusDisplay);
    
    const sim = {
        name: data.simulation_name,
        status: data.status,
        progress: data.progress || {}
    };
    
    let html = `<div class="simulation-status">`;
    html += `<small class="text-info"><strong>Simulation:</strong> ${sim.name}</small><br>`;
    html += `<small><strong>Status:</strong> <span class="badge bg-info">${sim.status.toUpperCase()}</span></small><br>`;
    
    if (sim.progress.current_step) {
        html += `<small><strong>Current Step:</strong> ${sim.progress.current_step}</small><br>`;
    }
    
    if (sim.progress.assets_processed !== undefined && sim.progress.total_assets !== undefined) {
        const total = sim.progress.total_assets || 1;
        const processed = sim.progress.assets_processed || 0;
        const percentage = Math.round((processed / total) * 100);
        
        html += `<small><strong>Assets:</strong> ${processed} / ${total} (${percentage}%)</small><br>`;
        html += `<div class="progress mt-2" style="height: 20px;">`;
        html += `<div class="progress-bar bg-success" role="progressbar" style="width: ${percentage}%" aria-valuenow="${percentage}" aria-valuemin="0" aria-valuemax="100"></div>`;
        html += `</div>`;
    }
    
    html += `<small class="text-muted d-block mt-2">Last updated: ${new Date(data.last_updated).toLocaleTimeString()}</small>`;
    html += `</div>`;
    
    simProgressContent.innerHTML = html;
}

function updateStatusIndicator(status) {
    const statusBadge = document.querySelector('#status-indicator .badge');
    if (statusBadge) {
        statusBadge.textContent = status;
        statusBadge.classList.remove('bg-warning', 'bg-success', 'bg-danger');
        
        if (status === 'Running') {
            statusBadge.classList.add('bg-primary');
        } else if (status === 'Idle') {
            statusBadge.classList.add('bg-warning');
        } else if (status === 'Error') {
            statusBadge.classList.add('bg-danger');
        } else {
            statusBadge.classList.add('bg-success');
        }
    }
}

function fetchBatchProgress() {
    fetch('/api/simulation/batch-progress')
        .then(response => response.json())
        .then(data => updateBatchProgressDisplay(data))
        .catch(error => console.error('Error fetching batch progress:', error));
}

function updateBatchProgressDisplay(data) {
    const batchProgressContent = document.getElementById('batch-progress-content');
    
    if (!data.has_active_simulation) {
        batchProgressContent.innerHTML = '<p class="text-muted">No active simulation</p>';
        return;
    }
    
    const batches = data.batches || [];
    
    if (batches.length === 0) {
        batchProgressContent.innerHTML = '<p class="text-muted">No batch data available</p>';
        return;
    }
    
    let html = '<div class="batch-progress-container">';
    
    batches.forEach(batch => {
        const completion = batch.completion_percentage || 0;
        const completed = batch.completed || 0;
        const total = batch.total || 0;
        
        html += `<div class="batch-card mb-3 p-3" style="background: #1a1a1a; border: 1px solid #404040; border-radius: 5px;">`;
        html += `<div class="d-flex justify-content-between align-items-center mb-2">`;
        html += `<strong class="text-info">Batch ${batch.batch}</strong>`;
        html += `<small class="text-muted">${completed}/${total}</small>`;
        html += `</div>`;
        html += `<div class="progress" style="height: 20px;">`;
        html += `<div class="progress-bar bg-success" role="progressbar" style="width: ${completion}%" aria-valuenow="${completion}" aria-valuemin="0" aria-valuemax="100"></div>`;
        html += `</div>`;
        html += `<small class="text-muted d-block mt-2">${completion.toFixed(1)}% complete</small>`;
        html += `</div>`;
    });
    
    html += '</div>';
    batchProgressContent.innerHTML = html;
}

function fetchSystemStatus() {
    fetch('/api/monitoring/performance')
        .then(response => response.json())
        .then(data => updateSystemStatusDisplay(data))
        .catch(error => console.error('Error fetching system status:', error));
}

function updateSystemStatusDisplay(data) {
    const statusContent = document.getElementById('system-status-content');
    if (!data.metrics) {
        statusContent.innerHTML = '<p class="text-muted">No data available</p>';
        return;
    }
    
    const metrics = data.metrics;
    let html = '<div class="metric-item mb-2">';
    
    html += `<small><strong>CPU:</strong> <span class="${getCpuClass(metrics.cpu_usage)}">${metrics.cpu_usage.toFixed(1)}%</span></small><br>`;
    html += `<small><strong>Memory:</strong> <span class="${getMemoryClass(metrics.memory_usage)}">${metrics.memory_usage.toFixed(1)}%</span></small><br>`;
    html += `<small><strong>Disk:</strong> <span class="${getDiskClass(metrics.disk_usage)}">${metrics.disk_usage.toFixed(1)}%</span></small><br>`;
    
    if (data.alerts && data.alerts.length > 0) {
        html += `<small class="text-warning mt-2"><strong>⚠ Alerts:</strong> ${data.alerts.length}</small>`;
    }
    
    html += '</div>';
    statusContent.innerHTML = html;
}

function getCpuClass(value) {
    if (value > 85) return 'text-danger';
    if (value > 70) return 'text-warning';
    return 'text-success';
}

function getMemoryClass(value) {
    if (value > 80) return 'text-danger';
    if (value > 65) return 'text-warning';
    return 'text-success';
}

function getDiskClass(value) {
    if (value > 90) return 'text-danger';
    if (value > 75) return 'text-warning';
    return 'text-success';
}

// ============= Simulation Performance Charts =============
function initializeSimulationPerformanceCharts() {
    console.log('Initializing simulation performance charts...');
    
    // Simulation Performance Chart (Dual Y-Axis)
    const perfCtx = document.getElementById('simulationPerformanceChart');
    if (perfCtx && !simulationPerformanceCharts.performance) {
        simulationPerformanceCharts.performance = new Chart(perfCtx, {
            type: 'line',
            data: {
                labels: simulationPerformanceData.timestamps,
                datasets: [
                    {
                        label: 'Avg Simulation Time (s)',
                        data: simulationPerformanceData.avgSimulationTime,
                        type: 'line',
                        borderColor: '#4ecdc4',
                        backgroundColor: 'rgba(78, 205, 196, 0.2)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.4,
                        pointRadius: 0,
                        pointHoverRadius: 5,
                        yAxisID: 'y-left'
                    },
                    {
                        label: 'Completion %',
                        data: simulationPerformanceData.completionPercentage,
                        type: 'line',
                        borderColor: '#ff6b6b',
                        backgroundColor: 'rgba(255, 107, 107, 0.1)',
                        borderWidth: 2,
                        fill: false,
                        tension: 0.4,
                        pointRadius: 0,
                        pointHoverRadius: 5,
                        yAxisID: 'y-right'
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false
                },
                plugins: {
                    legend: {
                        labels: {
                            color: '#b0b0b0',
                            font: { size: 12 }
                        }
                    },
                    tooltip: {
                        backgroundColor: 'rgba(0,0,0,0.8)',
                        padding: 12,
                        titleColor: '#fff',
                        bodyColor: '#fff'
                    }
                },
                scales: {
                    'y-left': {
                        type: 'linear',
                        position: 'left',
                        beginAtZero: true,
                        ticks: { 
                            color: '#4ecdc4',
                            callback: function(value) {
                                return value + 's';
                            }
                        },
                        grid: { color: '#404040' },
                        title: {
                            display: true,
                            text: 'Avg Simulation Time (seconds)',
                            color: '#4ecdc4'
                        }
                    },
                    'y-right': {
                        type: 'linear',
                        position: 'right',
                        beginAtZero: true,
                        max: 100,
                        ticks: { 
                            color: '#ff6b6b',
                            callback: function(value) {
                                return value + '%';
                            }
                        },
                        grid: { display: false },
                        title: {
                            display: true,
                            text: 'Completion %',
                            color: '#ff6b6b'
                        }
                    },
                    x: {
                        ticks: { 
                            color: '#b0b0b0',
                            maxRotation: 45,
                            minRotation: 0
                        },
                        grid: { color: '#404040' }
                    }
                }
            }
        });
    }

    // Throughput Chart
    const throughputCtx = document.getElementById('throughputChart');
    if (throughputCtx && !simulationPerformanceCharts.throughput) {
        simulationPerformanceCharts.throughput = new Chart(throughputCtx, {
            type: 'line',
            data: {
                labels: simulationPerformanceData.timestamps,
                datasets: [{
                    label: 'Assets/Minute',
                    data: simulationPerformanceData.throughput,
                    borderColor: '#f7b731',
                    backgroundColor: 'rgba(247, 183, 49, 0.2)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.4,
                    pointRadius: 2,
                    pointHoverRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        labels: {
                            color: '#b0b0b0',
                            font: { size: 12 }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: { color: '#b0b0b0' },
                        grid: { color: '#404040' },
                        title: {
                            display: true,
                            text: 'Assets per Minute',
                            color: '#b0b0b0'
                        }
                    },
                    x: {
                        ticks: { 
                            color: '#b0b0b0',
                            maxRotation: 45
                        },
                        grid: { color: '#404040' }
                    }
                }
            }
        });
    }

    // Success Rate Chart
    const successCtx = document.getElementById('successRateChart');
    if (successCtx && !simulationPerformanceCharts.successRate) {
        simulationPerformanceCharts.successRate = new Chart(successCtx, {
            type: 'line',
            data: {
                labels: simulationPerformanceData.timestamps,
                datasets: [
                    {
                        label: 'Success Rate %',
                        data: simulationPerformanceData.successRate,
                        borderColor: '#2ecc71',
                        backgroundColor: 'rgba(46, 204, 113, 0.2)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.4,
                        pointRadius: 2,
                        pointHoverRadius: 6
                    },
                    {
                        label: '95% Target',
                        data: Array(simulationPerformanceData.timestamps.length).fill(95),
                        borderColor: '#27ae60',
                        borderWidth: 2,
                        borderDash: [5, 5],
                        fill: false,
                        pointRadius: 0,
                        pointHoverRadius: 0
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        labels: {
                            color: '#b0b0b0',
                            font: { size: 12 }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 100,
                        ticks: { 
                            color: '#b0b0b0',
                            callback: function(value) {
                                return value + '%';
                            }
                        },
                        grid: { color: '#404040' },
                        title: {
                            display: true,
                            text: 'Success Rate %',
                            color: '#b0b0b0'
                        }
                    },
                    x: {
                        ticks: { 
                            color: '#b0b0b0',
                            maxRotation: 45
                        },
                        grid: { color: '#404040' }
                    }
                }
            }
        });
    }
}

function fetchSimulationPerformanceMetrics() {
    const simName = document.getElementById('sim-name-input')?.value || '';
    let url = '/api/monitoring/simulation-performance';
    
    // Add query parameters
    const params = new URLSearchParams();
    if (currentTimeRange) {
        params.append('time_range', currentTimeRange);
    }
    if (simName) {
        params.append('simulation_name', simName);
    }
    
    if (params.toString()) {
        url += '?' + params.toString();
    }
    
    fetch(url)
        .then(response => response.json())
        .then(data => updateSimulationPerformanceData(data))
        .catch(error => console.error('Error fetching simulation performance:', error));
}

function updateSimulationPerformanceData(data) {
    if (!data) {
        console.log('No data received');
        return;
    }
    
    const historical = data.historical_data || [];
    
    if (historical.length === 0) {
        console.log('No historical data available yet');
        // Show message on charts that no data is available
        document.getElementById('perf-chart-title').textContent = 
            'Simulation Performance - No Data Available';
        return;
    }
    
    // Clear existing data
    simulationPerformanceData.timestamps = [];
    simulationPerformanceData.avgSimulationTime = [];
    simulationPerformanceData.completionPercentage = [];
    simulationPerformanceData.throughput = [];
    simulationPerformanceData.successRate = [];
    
    // Populate from historical data
    historical.forEach(point => {
        const time = new Date(point.timestamp);
        const timeStr = time.toLocaleTimeString();
        
        simulationPerformanceData.timestamps.push(timeStr);
        simulationPerformanceData.avgSimulationTime.push(point.avg_simulation_time || 0);
        simulationPerformanceData.completionPercentage.push(point.completion_percentage || 0);
        simulationPerformanceData.throughput.push(point.throughput || 0);
        simulationPerformanceData.successRate.push(point.success_rate || 0);
    });
    
    // Update charts
    updateSimulationChart(simulationPerformanceCharts.performance, 
        simulationPerformanceData.timestamps,
        [simulationPerformanceData.avgSimulationTime, simulationPerformanceData.completionPercentage]);
    
    updateSimulationChart(simulationPerformanceCharts.throughput,
        simulationPerformanceData.timestamps,
        [simulationPerformanceData.throughput]);
    
    // Update success rate chart with threshold line
    if (simulationPerformanceCharts.successRate) {
        const thresholdData = Array(simulationPerformanceData.timestamps.length).fill(95);
        simulationPerformanceCharts.successRate.data.labels = simulationPerformanceData.timestamps;
        simulationPerformanceCharts.successRate.data.datasets[0].data = simulationPerformanceData.successRate;
        simulationPerformanceCharts.successRate.data.datasets[1].data = thresholdData;
        simulationPerformanceCharts.successRate.update('none');
    }
    
    // Update ETA in title
    if (data.eta_formatted) {
        document.getElementById('perf-chart-title').textContent = 
            `Simulation Performance - ETA: ${data.eta_formatted}`;
    } else if (data.latest) {
        document.getElementById('perf-chart-title').textContent = 
            'Simulation Performance - ETA: Calculating...';
    } else {
        document.getElementById('perf-chart-title').textContent = 
            'Simulation Performance - No Active Simulation';
    }
    
    // Update timestamp
    const now = new Date();
    document.getElementById('perf-update-time').textContent = now.toLocaleTimeString();
    
    console.log(`Updated charts with ${historical.length} data points`);
}

function updateSimulationChart(chart, labels, datasetsData) {
    if (!chart) return;
    
    chart.data.labels = labels;
    datasetsData.forEach((data, index) => {
        if (chart.data.datasets[index]) {
            chart.data.datasets[index].data = data;
        }
    });
    chart.update('none');
}

function startSimulationPerformancePolling() {
    // Polling is now handled by initializePerformanceMonitoring() on page load
    // This function kept for backward compatibility
    console.log('Performance monitoring polling is continuous (started on page load)');
}

// ============= Time Range & Export Functions =============
function setTimeRange(hours) {
    currentTimeRange = hours;
    
    // Update button states
    const buttons = document.querySelectorAll('#time-range-selector button');
    buttons.forEach(btn => btn.classList.remove('active'));
    event.target.classList.add('active');
    
    // Just filter displayed data, don't call API - use manual Refresh button for new data
    console.log(`Time range set to: ${hours || 'All'} (filters display only)`);
}

function exportData(format) {
    if (format === 'csv') {
        exportCSV();
    } else if (format === 'json') {
        exportJSON();
    } else if (format === 'png') {
        exportPNG();
    }
}

function exportCSV() {
    let csv = 'Timestamp,Avg Simulation Time (s),Completion %,Throughput (assets/min),Success Rate %\\n';
    
    for (let i = 0; i < simulationPerformanceData.timestamps.length; i++) {
        csv += `${simulationPerformanceData.timestamps[i]},`;
        csv += `${simulationPerformanceData.avgSimulationTime[i]},`;
        csv += `${simulationPerformanceData.completionPercentage[i]},`;
        csv += `${simulationPerformanceData.throughput[i]},`;
        csv += `${simulationPerformanceData.successRate[i]}\\n`;
    }
    
    downloadFile(csv, 'simulation-performance.csv', 'text/csv');
    console.log('Exported CSV data');
}

function exportJSON() {
    const jsonData = {
        exported_at: new Date().toISOString(),
        time_range: currentTimeRange || 'all',
        data_points: simulationPerformanceData.timestamps.length,
        metrics: []
    };
    
    for (let i = 0; i < simulationPerformanceData.timestamps.length; i++) {
        jsonData.metrics.push({
            timestamp: simulationPerformanceData.timestamps[i],
            avg_simulation_time: simulationPerformanceData.avgSimulationTime[i],
            completion_percentage: simulationPerformanceData.completionPercentage[i],
            throughput: simulationPerformanceData.throughput[i],
            success_rate: simulationPerformanceData.successRate[i]
        });
    }
    
    downloadFile(JSON.stringify(jsonData, null, 2), 'simulation-performance.json', 'application/json');
    console.log('Exported JSON data');
}

function exportPNG() {
    // Export the main performance chart as PNG
    const canvas = document.getElementById('simulationPerformanceChart');
    if (!canvas) {
        alert('Chart not found');
        return;
    }
    
    canvas.toBlob(function(blob) {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.download = 'simulation-performance-chart.png';
        link.href = url;
        link.click();
        URL.revokeObjectURL(url);
        console.log('Exported PNG chart');
    });
}

function downloadFile(content, filename, mimeType) {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.download = filename;
    link.href = url;
    link.click();
    URL.revokeObjectURL(url);
}

// ============= Logs =============
// Global variable to track current log type
let currentLogType = 'user';

function loadLogs() {
    fetch('/api/logs/get-current?log_type=user')  // Fetch all logs from user_logs (improved format)
        .then(response => response.json())
        .then(data => {
            const logsContent = document.getElementById('logs-content');
            if (data.lines && data.lines.length > 0) {
                logsContent.textContent = data.lines.join('\n');
                logsContent.scrollTop = logsContent.scrollHeight;
            } else {
                logsContent.textContent = 'No logs available';
            }
        })
        .catch(error => {
            console.error('Error loading logs:', error);
            document.getElementById('logs-content').textContent = 'Error loading logs';
        });
}

function getCurrentLogs() {
    console.log('Refreshing logs and fetching ' + currentLogType + ' logs...');
    
    // First, refresh the log files
    fetch('/api/logs/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(response => response.json())
    .then(refreshData => {
        console.log('Logs refreshed:', refreshData);
        
        // Refresh available batch logs in dropdown
        fetchAvailableBatchLogs();
        
        // Then fetch the current logs
        return fetch('/api/logs/get-current?lines=100&log_type=' + currentLogType);
    })
    .then(response => response.json())
    .then(data => {
        const logsContent = document.getElementById('logs-content');
        if (data.lines && data.lines.length > 0) {
            logsContent.textContent = data.lines.join('\n');
            logsContent.scrollTop = logsContent.scrollHeight;
            console.log('Loaded ' + data.count + ' log lines');
        } else {
            logsContent.textContent = data.message || 'No logs available';
        }
    })
    .catch(error => {
        console.error('Error fetching logs:', error);
        document.getElementById('logs-content').textContent = 'Error fetching logs: ' + error.message;
    });
}

// Add event listener for log type dropdown
document.addEventListener('DOMContentLoaded', function() {
    const logTypeSelector = document.getElementById('log-type-selector');
    if (logTypeSelector) {
        // Fetch available batch logs and populate dropdown
        fetchAvailableBatchLogs();
        
        logTypeSelector.addEventListener('change', function() {
            currentLogType = this.value;
            console.log('Switching to ' + currentLogType + ' logs...');
            
            // Fetch the new log type
            fetch('/api/logs/get-current?lines=100&log_type=' + currentLogType)
                .then(response => response.json())
                .then(data => {
                    const logsContent = document.getElementById('logs-content');
                    if (data.lines && data.lines.length > 0) {
                        logsContent.textContent = data.lines.join('\n');
                        logsContent.scrollTop = logsContent.scrollHeight;
                        console.log('Loaded ' + data.count + ' ' + currentLogType + ' log lines');
                    } else {
                        logsContent.textContent = data.message || 'No logs available';
                    }
                })
                .catch(error => {
                    console.error('Error fetching logs:', error);
                    document.getElementById('logs-content').textContent = 'Error fetching logs: ' + error.message;
                });
        });
    }
});

function fetchAvailableBatchLogs() {
    fetch('/api/logs/available-batches')
        .then(response => response.json())
        .then(data => {
            const logTypeSelector = document.getElementById('log-type-selector');
            if (logTypeSelector && data.batches && data.batches.length > 0) {
                // Save current selection before rebuilding
                const currentSelection = logTypeSelector.value;
                
                // Remove existing batch options
                const existingBatchOptions = logTypeSelector.querySelectorAll('option[value^="batch_"]');
                existingBatchOptions.forEach(opt => opt.remove());
                
                // Add new batch options
                data.batches.forEach(batchNum => {
                    const option = document.createElement('option');
                    option.value = `batch_${batchNum}`;
                    option.textContent = `Batch ${batchNum} Logs`;
                    logTypeSelector.appendChild(option);
                });
                
                // Restore previous selection if it still exists
                const optionExists = Array.from(logTypeSelector.options).some(opt => opt.value === currentSelection);
                if (optionExists) {
                    logTypeSelector.value = currentSelection;
                }
            }
        })
        .catch(error => {
            console.error('Error fetching available batch logs:', error);
        });
}

function clearLogs() {
    document.getElementById('logs-content').textContent = '';
}

// ============= Periodic Updates =============
function startPeriodicUpdates() {
    // Always fetch performance metrics every 30 seconds, regardless of tab visibility
    setInterval(() => {
        fetchPerformanceMetrics();
    }, 30000);
    // Removed 10-second log polling to prevent auto-clearing when user views logs
}

// ============= Simulation API Calls =============
function autorunSimulation() {
    if (!confirm('Start autorun simulation?')) return;
    
    setSimulationStatus('Running', 'warning');
    
    fetch('/api/simulation/autorun_simulation', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(response => response.json())
    .then(data => {
        alert(data.message);
        // Immediately fetch updated status from server
        fetchCurrentSimulationStatus();
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Failed to start autorun simulation');
        setSimulationStatus('Error', 'danger');
    });
}

function stopSimulation() {
    if (!confirm('Stop current simulation?')) return;
    
    fetch('/api/simulation/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(response => response.json())
    .then(data => {
        if (data.message) {
            alert(data.message);
        } else if (data.error) {
            alert('Error: ' + data.error);
        }
        setSimulationStatus('Stopped', 'info');
        // Refresh the simulation status display
        setTimeout(() => {
            fetchCurrentSimulationStatus();
            fetchBatchProgress();
        }, 500);
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Failed to stop simulation: ' + error.message);
    });
}

async function startSimulation() {
    const asset_geojson_file = document.getElementById('startsim_asset_geojson_file').files[0];
    const metadata_csv_file = document.getElementById('startsim_metadata_csv_file').files[0];
    const simulation_name = document.getElementById('startsim_simulation_name').value;
    const num_cores = document.getElementById('startsim_num_cores').value;
    const hpc_mode = document.getElementById('startsim_hpc_mode').checked;
    const shared_storage = document.getElementById('startsim_shared_storage').value;

    if (!(asset_geojson_file && metadata_csv_file)) {
        alert('Please upload both GeoJSON and CSV files.');
        return;
    }

    if (!simulation_name) {
        alert('Please enter a simulation name.');
        return;
    }

    if (hpc_mode && !shared_storage) {
        alert('Shared storage path is required for HPC mode.');
        return;
    }

    const configData = {
        weekday_start_time: document.getElementById('weekday_start_time').value,
        weekday_duration: document.getElementById('weekday_duration').value,
        weekend_start_time: document.getElementById('weekend_start_time').value,
        weekend_duration: document.getElementById('weekend_duration').value,
        system_type: document.getElementById('system_type').value,
        heating_system_fuel_type: 'electricity',
        constructions: {
            wall: {
                material: 'Super Insulated Wall',
                r_value: parseFloat(document.getElementById('wall_r_value').value)
            },
            roof: {
                material: 'Super Insulated Roof',
                r_value: parseFloat(document.getElementById('roof_r_value').value)
            }
        }
    };

    const formData = new FormData();
    formData.append('simulation_name', simulation_name);
    formData.append('asset_geojson_file', asset_geojson_file);
    formData.append('metadata_csv_file', metadata_csv_file);
    formData.append('config_data', JSON.stringify(configData));
    formData.append('num_cores', num_cores);
    formData.append('hpc_mode', hpc_mode);
    if (hpc_mode) {
        formData.append('shared_storage', shared_storage);
    }
    formData.append('keep_dirs', document.getElementById('startsim_keep_dirs').checked);

    try {
        setSimulationStatus('Starting...', 'info');
        const response = await fetch('/api/simulation/start', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const errorData = await response.json();
            alert(`Error: ${errorData.error}`);
            setSimulationStatus('Error', 'danger');
            return;
        }

        alert('Simulation started successfully!');
        setSimulationStatus('Running', 'warning');
        switchTab('dashboard');
        // Immediately fetch updated status from server (like autorun does)
        fetchCurrentSimulationStatus();
        fetchBatchProgress();
    } catch (error) {
        console.error('Error:', error);
        alert('Failed to start simulation');
        setSimulationStatus('Error', 'danger');
    }
}

async function recoverSimulation() {
    const corrupted_name = document.getElementById('corrupted_simulation_name').value;
    const recover_name = document.getElementById('recover_simulation_name').value;
    const num_cores = document.getElementById('recover_num_cores').value;

    if (!corrupted_name || !recover_name) {
        alert('Please enter simulation names.');
        return;
    }

    const formData = new FormData();
    formData.append('corrupted_simulation_name', corrupted_name);
    formData.append('recover_batch_id', document.getElementById('recover_batch_id').value);
    formData.append('recover_simulation_name', recover_name);
    formData.append('num_cores', num_cores);
    formData.append('keep_dirs', document.getElementById('recover_keep_dirs').checked);

    try {
        setSimulationStatus('Recovering...', 'info');
        const response = await fetch('/api/diagnostics/recovery', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const errorData = await response.json();
            alert(`Error: ${errorData.error}`);
            setSimulationStatus('Error', 'danger');
            return;
        }

        alert('Simulation recovery started!');
        setSimulationStatus('Running', 'warning');
    } catch (error) {
        console.error('Error:', error);
        alert('Failed to recover simulation');
        setSimulationStatus('Error', 'danger');
    }
}

async function getSimulationStatus() {
    const simulation_name = document.getElementById('status_simulation_name').value;
    const batchId = document.getElementById('status_batch_id').value;

    if (!simulation_name) {
        alert('Please enter a simulation name.');
        return;
    }

    let url = `/api/simulation/status/${simulation_name}`;
    if (batchId) {
        url += `?batch_id=${batchId}`;
    }

    try {
        const response = await fetch(url);
        if (!response.ok) {
            const errorData = await response.json();
            alert(`Error: ${errorData.error}`);
            return;
        }
        const data = await response.json();
        console.log('Simulation Status:', data);
        displaySimulationStatus(data);
        switchTab('dashboard');
    } catch (error) {
        console.error('Error:', error);
        alert('Failed to fetch simulation status');
    }
}

function displaySimulationStatus(status) {
    const progressContent = document.getElementById('sim-progress-content');
    let html = `
        <div class="mb-3">
            <p><strong>Simulation:</strong> ${status.simulation_name || 'Unknown'}</p>
            <p><strong>Status:</strong> <span class="badge bg-info">${status.status || 'Unknown'}</span></p>
            ${status.progress ? `
                <p><strong>Progress:</strong></p>
                <div class="progress">
                    <div class="progress-bar" role="progressbar" style="width: ${status.progress}%">${status.progress}%</div>
                </div>
            ` : ''}
        </div>
    `;
    progressContent.innerHTML = html;
}

function setSimulationStatus(status, type) {
    const statusBadge = document.querySelector('#status-indicator .badge');
    statusBadge.className = `badge bg-${type}`;
    statusBadge.textContent = status;
}

async function deleteSimulation() {
    const simulation_name = document.getElementById('delete_simulation_name').value;

    if (!simulation_name) {
        alert('Please enter a simulation name.');
        return;
    }

    if (!confirm(`Delete simulation "${simulation_name}"? This cannot be undone.`)) {
        return;
    }

    try {
        const response = await fetch(`/api/simulation/delete/${simulation_name}`, {
            method: 'POST'
        });

        if (!response.ok) {
            const errorData = await response.json();
            alert(`Error: ${errorData.error}`);
            return;
        }

        alert('Simulation deleted successfully!');
        document.getElementById('delete_simulation_name').value = '';
    } catch (error) {
        console.error('Error:', error);
        alert('Failed to delete simulation');
    }
}

// ============= Assets Tab Functions =============
async function loadSimulationAssets() {
    const simulationName = document.getElementById('browse_simulation_name').value;
    
    if (!simulationName) {
        alert('Please enter a simulation name');
        return;
    }
    
    // Show loading message
    const container = document.getElementById('assets-list');
    container.innerHTML = '<p class="text-muted"><i class="bi bi-hourglass-split"></i> Loading assets...</p>';
    
    try {
        const response = await fetch(`/api/simulation/${simulationName}/assets`);
        const data = await response.json();
        
        if (!response.ok) {
            container.innerHTML = `<div class="alert alert-danger"><i class="bi bi-exclamation-triangle"></i> ${data.error}</div>`;
            return;
        }
        
        displayAssetsList(data.assets, simulationName);
    } catch (error) {
        console.error('Error:', error);
        container.innerHTML = '<div class="alert alert-danger"><i class="bi bi-exclamation-triangle"></i> Failed to load assets</div>';
    }
}

function displayAssetsList(assets, simulationName) {
    const container = document.getElementById('assets-list');
    
    if (assets.length === 0) {
        container.innerHTML = '<div class="alert alert-warning"><i class="bi bi-info-circle"></i> No assets found for this simulation</div>';
        return;
    }
    
    // Store assets globally for pagination and filtering
    window.allAssets = assets;
    window.filteredAssets = assets;
    window.currentSimulationName = simulationName;
    window.currentPage = 1;
    window.itemsPerPage = 50;
    
    let html = `<div class="alert alert-info mb-3"><i class="bi bi-info-circle"></i> Found <strong>${assets.length}</strong> assets for simulation: <strong>${simulationName}</strong></div>`;
    
    // Add search bar
    html += `<div class="mb-3">
        <div class="input-group mb-2">
            <span class="input-group-text"><i class="bi bi-search"></i></span>
            <input type="text" class="form-control" id="assetSearchInput" placeholder="Search by asset ID or name..." onkeyup="searchAssets()">
            <button class="btn btn-outline-secondary" onclick="clearAssetSearch()">Clear</button>
        </div>
        <div class="d-flex justify-content-between align-items-center">
            <small class="form-text text-muted">Showing <span id="assetStart">1</span>-<span id="assetEnd">50</span> of <span id="assetTotal">${assets.length}</span> assets</small>
            <div>
                <label class="form-label mb-0">Items per page:</label>
                <select class="form-select form-select-sm" id="itemsPerPageSelect" style="width: auto; display: inline-block;" onchange="changeItemsPerPage()">
                    <option value="25">25</option>
                    <option value="50" selected>50</option>
                    <option value="100">100</option>
                </select>
            </div>
        </div>
    </div>`;
    
    html += '<div class="table-responsive" id="tableContainer">';
    html += '<table class="table table-dark table-striped table-hover">';
    html += '<thead><tr>';
    html += '<th>Asset ID</th>';
    html += '<th>Name</th>';
    html += '<th>Floor Area (m²)</th>';
    html += '<th>Stories</th>';
    html += '<th>Building Type</th>';
    html += '<th style="text-align: center;">Action</th>';
    html += '</tr></thead><tbody id="assetTableBody">';
    html += '</tbody></table></div>';
    
    // Add pagination controls
    html += `<nav aria-label="Asset pagination" class="mt-3">
        <div class="d-flex justify-content-between align-items-center mb-3">
            <div>
                <button class="btn btn-sm btn-outline-secondary" id="prevBtn" onclick="previousPage()"><i class="bi bi-chevron-left"></i> Previous</button>
                <button class="btn btn-sm btn-outline-secondary" id="nextBtn" onclick="nextPage()">Next <i class="bi bi-chevron-right"></i></button>
            </div>
            <div>
                <label class="form-label mb-0 me-2">Go to page:</label>
                <div style="display: inline-flex; gap: 5px;">
                    <input type="number" class="form-control form-control-sm" id="pageInput" style="width: 70px;" min="1">
                    <button class="btn btn-sm btn-outline-primary" onclick="goToPage()">Go</button>
                </div>
            </div>
            <div>
                <span id="pageInfo">Page 1 of 1</span>
            </div>
        </div>
    </nav>`;
    
    container.innerHTML = html;
    renderAssetPage();
}

function renderAssetPage() {
    const startIdx = (window.currentPage - 1) * window.itemsPerPage;
    const endIdx = startIdx + window.itemsPerPage;
    const pageAssets = window.filteredAssets.slice(startIdx, endIdx);
    
    let html = '';
    pageAssets.forEach(asset => {
        html += `<tr>
            <td><code>${asset.asset_id}</code></td>
            <td>${asset.asset_name}</td>
            <td>${asset.floor_area}</td>
            <td>${asset.number_of_stories}</td>
            <td><span class="badge bg-secondary">${asset.building_type}</span></td>
            <td style="text-align: center;">
                <button class="btn btn-sm btn-primary" onclick="viewAssetConfig('${window.currentSimulationName}', '${asset.asset_id}')">
                    <i class="bi bi-eye"></i> View
                </button>
            </td>
        </tr>`;
    });
    
    document.getElementById('assetTableBody').innerHTML = html;
    updatePaginationControls();
}

function updatePaginationControls() {
    const totalPages = Math.ceil(window.filteredAssets.length / window.itemsPerPage);
    const startIdx = (window.currentPage - 1) * window.itemsPerPage + 1;
    const endIdx = Math.min(window.currentPage * window.itemsPerPage, window.filteredAssets.length);
    
    // Update info text
    document.getElementById('assetStart').textContent = startIdx;
    document.getElementById('assetEnd').textContent = endIdx;
    document.getElementById('assetTotal').textContent = window.filteredAssets.length;
    document.getElementById('pageInfo').textContent = `Page ${window.currentPage} of ${totalPages}`;
    document.getElementById('pageInput').value = window.currentPage;
    document.getElementById('pageInput').max = totalPages;
    
    // Update button states
    document.getElementById('prevBtn').disabled = window.currentPage === 1;
    document.getElementById('nextBtn').disabled = window.currentPage === totalPages;
}

function searchAssets() {
    const searchInput = document.getElementById('assetSearchInput').value.toLowerCase();
    
    window.filteredAssets = window.allAssets.filter(asset => {
        return asset.asset_id.toLowerCase().includes(searchInput) || 
               asset.asset_name.toLowerCase().includes(searchInput);
    });
    
    window.currentPage = 1;
    renderAssetPage();
}

function clearAssetSearch() {
    document.getElementById('assetSearchInput').value = '';
    window.filteredAssets = window.allAssets;
    window.currentPage = 1;
    renderAssetPage();
}

function changeItemsPerPage() {
    window.itemsPerPage = parseInt(document.getElementById('itemsPerPageSelect').value);
    window.currentPage = 1;
    renderAssetPage();
}

function previousPage() {
    if (window.currentPage > 1) {
        window.currentPage--;
        renderAssetPage();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }
}

function nextPage() {
    const totalPages = Math.ceil(window.filteredAssets.length / window.itemsPerPage);
    if (window.currentPage < totalPages) {
        window.currentPage++;
        renderAssetPage();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }
}

function goToPage() {
    const pageNum = parseInt(document.getElementById('pageInput').value);
    const totalPages = Math.ceil(window.filteredAssets.length / window.itemsPerPage);
    
    if (pageNum >= 1 && pageNum <= totalPages) {
        window.currentPage = pageNum;
        renderAssetPage();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    } else {
        alert(`Please enter a page number between 1 and ${totalPages}`);
    }
}

async function viewAssetConfig(simulationName, assetId) {
    try {
        const response = await fetch(`/api/asset/config/${simulationName}/${assetId}`);
        
        if (!response.ok) {
            const errorData = await response.json();
            alert(`Error: ${errorData.error}`);
            return;
        }
        
        const text = await response.text();
        const jsonData = JSON.parse(text);
        
        // Store for download functionality
        window.currentAssetConfig = { assetId, jsonData, simulationName };
        
        displayAssetConfig(assetId, jsonData, simulationName);
    } catch (error) {
        console.error('Error:', error);
        alert('Failed to load asset configuration');
    }
}

function displayAssetConfig(assetId, jsonData, simulationName) {
    const container = document.getElementById('assets-list');
    const jsonString = JSON.stringify(jsonData, null, 2);
    const highlightedJson = syntaxHighlight(jsonString);
    
    const html = `
        <div class="card bg-dark text-light">
            <div class="card-header d-flex justify-content-between align-items-center">
                <h5 class="mb-0">Asset Configuration: <code>${assetId}</code></h5>
                <div>
                    <button class="btn btn-sm btn-success me-2" onclick="downloadCurrentAssetConfig()">
                        <i class="bi bi-download"></i> Download JSON
                    </button>
                    <button class="btn btn-sm btn-secondary" onclick="loadSimulationAssets()">
                        <i class="bi bi-arrow-left"></i> Back to List
                    </button>
                </div>
            </div>
            <div class="card-body">
                <pre class="bg-dark text-light p-3 rounded" style="max-height: 600px; overflow-y: auto;"><code>${highlightedJson}</code></pre>
            </div>
        </div>
    `;
    
    container.innerHTML = html;
}

function syntaxHighlight(json) {
    json = json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return json.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, function (match) {
        let cls = 'text-info'; // numbers
        if (/^"/.test(match)) {
            if (/:$/.test(match)) {
                cls = 'text-warning'; // keys
            } else {
                cls = 'text-success'; // strings
            }
        } else if (/true|false/.test(match)) {
            cls = 'text-primary'; // booleans
        } else if (/null/.test(match)) {
            cls = 'text-muted'; // null
        }
        return '<span class="' + cls + '">' + match + '</span>';
    });
}

function downloadCurrentAssetConfig() {
    if (!window.currentAssetConfig) {
        alert('No asset configuration loaded');
        return;
    }
    
    const { assetId, jsonData } = window.currentAssetConfig;
    const jsonString = JSON.stringify(jsonData, null, 2);
    const blob = new Blob([jsonString], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${assetId}_config.json`;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}
