console.log("PowerTwin Solver UI v2.0 loaded");

// ============= Global Variables =============
let performanceCharts = {
    cpu: null,
    memory: null,
    disk: null,
    db: null
};

let performanceData = {
    timestamps: [],
    cpu: [],
    memory: [],
    disk: [],
    dbQueries: [],
    dbTime: []
};

const MAX_HISTORY_POINTS = 60;

// ============= Initialization =============
document.addEventListener('DOMContentLoaded', function() {
    console.log("DOM loaded, initializing...");
    loadSystemTypes();
    loadBuildingTypes();
    initializeDashboard();
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
    
    if (tabName === 'performance' && !performanceCharts.cpu) {
        setTimeout(() => {
            initializePerformanceCharts();
            fetchPerformanceMetrics();
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
    
    // Update status indicator
    updateStatusIndicator('Running');
    
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

// ============= Performance Charts =============
function initializePerformanceCharts() {
    const chartConfig = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                labels: {
                    color: '#b0b0b0',
                    font: { size: 11 }
                }
            }
        },
        scales: {
            y: {
                beginAtZero: true,
                max: 100,
                ticks: { color: '#b0b0b0' },
                grid: { color: '#404040' }
            },
            x: {
                ticks: { color: '#b0b0b0' },
                grid: { color: '#404040' }
            }
        }
    };

    const cpuCtx = document.getElementById('cpuChart');
    if (cpuCtx && !performanceCharts.cpu) {
        performanceCharts.cpu = new Chart(cpuCtx, {
            type: 'line',
            data: {
                labels: performanceData.timestamps,
                datasets: [{
                    label: 'CPU Usage (%)',
                    data: performanceData.cpu,
                    borderColor: '#ff6b6b',
                    backgroundColor: 'rgba(255, 107, 107, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.4,
                    pointRadius: 0,
                    pointHoverRadius: 5
                }]
            },
            options: chartConfig
        });
    }

    const memoryCtx = document.getElementById('memoryChart');
    if (memoryCtx && !performanceCharts.memory) {
        performanceCharts.memory = new Chart(memoryCtx, {
            type: 'line',
            data: {
                labels: performanceData.timestamps,
                datasets: [{
                    label: 'Memory Usage (%)',
                    data: performanceData.memory,
                    borderColor: '#4ecdc4',
                    backgroundColor: 'rgba(78, 205, 196, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.4,
                    pointRadius: 0,
                    pointHoverRadius: 5
                }]
            },
            options: chartConfig
        });
    }

    const diskCtx = document.getElementById('diskChart');
    if (diskCtx && !performanceCharts.disk) {
        performanceCharts.disk = new Chart(diskCtx, {
            type: 'line',
            data: {
                labels: performanceData.timestamps,
                datasets: [{
                    label: 'Disk Usage (%)',
                    data: performanceData.disk,
                    borderColor: '#f7b731',
                    backgroundColor: 'rgba(247, 183, 49, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.4,
                    pointRadius: 0,
                    pointHoverRadius: 5
                }]
            },
            options: chartConfig
        });
    }

    const dbCtx = document.getElementById('dbChart');
    if (dbCtx && !performanceCharts.db) {
        performanceCharts.db = new Chart(dbCtx, {
            type: 'bar',
            data: {
                labels: performanceData.timestamps,
                datasets: [{
                    label: 'Avg Query Time (ms)',
                    data: performanceData.dbTime,
                    backgroundColor: '#5f27cd',
                    borderColor: '#8b5fc7',
                    borderWidth: 1
                }]
            },
            options: {
                ...chartConfig,
                scales: {
                    ...chartConfig.scales,
                    y: {
                        ...chartConfig.scales.y,
                        max: undefined
                    }
                }
            }
        });
    }
}

function fetchPerformanceMetrics() {
    fetch('/api/monitoring/performance')
        .then(response => response.json())
        .then(data => updatePerformanceData(data))
        .catch(error => console.error('Error fetching performance metrics:', error));
}

function updatePerformanceData(data) {
    if (!data || (!data.metrics && !data.system)) return;

    const timestamp = new Date().toLocaleTimeString();
    
    // Handle both old and new data formats
    let metrics = data.metrics || data.system || {};
    let dbMetrics = data.database_metrics || data.database || {};

    // Extract nested percentage values from system metrics
    const cpuValue = metrics.cpu?.percent ?? metrics.cpu_usage ?? 0;
    const memoryValue = metrics.memory?.percent ?? metrics.memory_usage ?? 0;
    const diskValue = metrics.disk?.percent ?? metrics.disk_usage ?? 0;
    const dbTimeValue = dbMetrics.avg_query_time_ms ?? dbMetrics.avg_query_time ?? 0;

    // Debug logging (can be removed after verification)
    if (performanceData.timestamps.length === 0) {
        console.log('First performance data received:', { cpu: cpuValue, memory: memoryValue, disk: diskValue, dbTime: dbTimeValue });
    }

    if (performanceData.timestamps.length >= MAX_HISTORY_POINTS) {
        performanceData.timestamps.shift();
        performanceData.cpu.shift();
        performanceData.memory.shift();
        performanceData.disk.shift();
        performanceData.dbTime.shift();
    }

    performanceData.timestamps.push(timestamp);
    performanceData.cpu.push(cpuValue);
    performanceData.memory.push(memoryValue);
    performanceData.disk.push(diskValue);

    performanceData.dbTime.push(dbTimeValue);

    updateChart(performanceCharts.cpu, performanceData.timestamps, performanceData.cpu);
    updateChart(performanceCharts.memory, performanceData.timestamps, performanceData.memory);
    updateChart(performanceCharts.disk, performanceData.timestamps, performanceData.disk);
    updateChart(performanceCharts.db, performanceData.timestamps, performanceData.dbTime);

    document.getElementById('perf-update-time').textContent = timestamp;

    if (data.recent_alerts && data.recent_alerts.length > 0) {
        updateAlertsDisplay(data.recent_alerts);
    }
}

function updateChart(chart, labels, data) {
    if (chart) {
        chart.data.labels = labels;
        chart.data.datasets[0].data = data;
        chart.update('none');
    }
}

function updateAlertsDisplay(alerts) {
    const alertsContent = document.getElementById('alerts-content');
    if (alerts.length === 0) {
        alertsContent.innerHTML = '<p class="text-muted">No alerts at this time</p>';
        return;
    }

    let html = '<div>';
    alerts.forEach(alert => {
        const alertClass = alert.severity === 'WARNING' ? 'alert-warning' : 'alert-info';
        html += `<div class="alert ${alertClass} mb-2 py-2 px-3"><small><strong>${alert.severity}:</strong> ${alert.message}</small></div>`;
    });
    html += '</div>';
    alertsContent.innerHTML = html;
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
    formData.append('batch_id', document.getElementById('recover_batch_id').value);
    formData.append('recovery_simulation_name', recover_name);
    formData.append('num_cores', num_cores);
    formData.append('keep_dirs', document.getElementById('recover_keep_dirs').checked);

    try {
        setSimulationStatus('Recovering...', 'info');
        const response = await fetch('/api/simulation/recover', {
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

async function getAssetConfig() {
    const asset_id = document.getElementById('get_asset_id_config').value;
    const simulation_name = document.getElementById('get_simulation_name_config').value;

    if (!asset_id || !simulation_name) {
        alert('Please enter asset ID and simulation name.');
        return;
    }

    try {
        const response = await fetch(`/api/asset/config/${simulation_name}/${asset_id}`);

        if (!response.ok) {
            const errorData = await response.json();
            alert(`Error: ${errorData.error}`);
            return;
        }

        const blob = await response.blob();
        const element = document.createElement('a');
        element.setAttribute('href', URL.createObjectURL(blob));
        element.setAttribute('download', `${asset_id}_config.json`);
        element.style.display = 'none';
        document.body.appendChild(element);
        element.click();
        document.body.removeChild(element);
    } catch (error) {
        console.error('Error:', error);
        alert('Failed to get asset configuration');
    }
}
