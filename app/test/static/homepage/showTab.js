// Load system types from JSON file and populate the dropdown
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

// Load building types from JSON file and populate the dropdown
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


function showTab(tabId) {
    console.log(`Showing tab: ${tabId}`);
    document.querySelectorAll('.tab').forEach(tab => {
        tab.classList.remove('active');
    });
    document.getElementById(tabId).classList.add('active');
}

module.exports = showTab;
