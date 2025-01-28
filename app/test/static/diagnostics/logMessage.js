async function logMessage(message, type = 'log') {
    
    try {
        await fetch('/api/log', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ message, type })
        });
    
    } catch (error) {
        console.error('Error logging message:', error);
    }
}

console.log = function(message) {
    logMessage(message, 'log');
};

console.error = function(message) {
    logMessage(message, 'error');
};