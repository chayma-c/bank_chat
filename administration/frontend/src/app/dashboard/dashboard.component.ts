export class DashboardComponent {
    appTitle = 'The Sentinel';
    searchQuery = '';

    menuItems = [
        { label: 'Dashboard', icon: 'dashboard', active: true },
        { label: 'Rule Management', icon: 'rule', active: false },
        { label: 'Decision Logs', icon: 'history_edu', active: false },
        { label: 'Model Configuration', icon: 'query_stats', active: false }
    ];

    metrics = [
        {
            label: 'Total Risk Exposure',
            value: '$2.4M',
            subtext: '+14.2% vs yesterday'
        },
        {
            label: 'Alert Volume',
            value: '1,284',
            subtext: '82% Capacity Reached'
        },
        {
            label: 'False Positive Rate',
            value: '3.8%',
            subtext: '-0.4% Improving'
        }
    ];

    investigations = [
        {
            id: '#TRX-89210-99',
            name: 'Julianne DeSilva',
            score: '94 - CRITICAL',
            trigger: 'Anomalous Geo-location Sweep'
        }
    ];
}