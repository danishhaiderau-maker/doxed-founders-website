"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.FOUNDER_SHORTCUT_VIEW_IDS = exports.FOUNDER_SHORTCUT_SURFACES = void 0;
exports.shortcutEntries = shortcutEntries;
exports.FOUNDER_SHORTCUT_SURFACES = [
    'agents',
    'ship',
    'node',
    'connections',
    'remote',
];
exports.FOUNDER_SHORTCUT_VIEW_IDS = {
    agents: 'founderOs.agents',
    ship: 'founderOs.ship',
    node: 'founderOs.node',
    connections: 'founderOs.connections',
    remote: 'founderOs.remote',
};
function shortcutEntries(surface, state) {
    switch (surface) {
        case 'agents':
            return [
                {
                    id: 'start-task',
                    label: 'Start or continue a task',
                    description: state.workspaceName,
                    icon: 'comment-discussion',
                    command: 'founderOs.openChat',
                },
                {
                    id: 'review-changes',
                    label: 'Review workspace changes',
                    description: 'Source Control',
                    icon: 'diff',
                    command: 'workbench.view.scm',
                },
                {
                    id: 'recent-activity',
                    label: 'Recent AI activity',
                    description: state.modeLabel,
                    icon: 'history',
                    command: 'founderOs.recentGatewayMetadata',
                },
                {
                    id: 'project-brief',
                    label: 'Project brief',
                    description: 'Completed, blocked, and next work',
                    icon: 'notebook',
                    command: 'founderOs.openProjectBrief',
                },
            ];
        case 'ship':
            return [
                {
                    id: 'source-control',
                    label: 'Review and commit changes',
                    description: state.workspaceName,
                    icon: 'source-control',
                    command: 'workbench.view.scm',
                },
                {
                    id: 'run-task',
                    label: 'Run a build or test task',
                    icon: 'play',
                    command: 'workbench.action.tasks.runTask',
                },
                {
                    id: 'terminal',
                    label: 'Open terminal',
                    icon: 'terminal',
                    command: 'workbench.action.terminal.toggleTerminal',
                },
                {
                    id: 'daily-quality-review',
                    label: 'Daily quality review',
                    description: 'Health, links, integrations, and release evidence',
                    icon: 'verified-filled',
                    command: 'founderOs.runDailyQualityReview',
                },
                {
                    id: 'deployments',
                    label: 'Deployment connections',
                    description: 'GitHub, Vercel, Railway, Neon',
                    icon: 'cloud-upload',
                    command: 'founderOs.openConnectionsView',
                },
            ];
        case 'node':
            return [
                {
                    id: 'node-status',
                    label: state.connected ? 'Founder Node connected' : 'Founder Node needs sign-in',
                    description: state.modeLabel,
                    icon: state.connected ? 'pass-filled' : 'warning',
                    command: 'founderOs.manage',
                },
                {
                    id: 'manage-node',
                    label: 'Manage Founder Node',
                    icon: 'server-process',
                    command: 'founderOs.manage',
                },
                {
                    id: 'reconnect-node',
                    label: 'Reconnect this IDE',
                    icon: 'debug-disconnect',
                    command: 'founderOs.connectFounderOs',
                },
                {
                    id: 'node-diagnostics',
                    label: 'Open gateway diagnostics',
                    icon: 'output',
                    command: 'founderOs.showGatewayMetadata',
                },
            ];
        case 'connections':
            return [
                {
                    id: 'founder-settings',
                    label: 'Founder Settings',
                    description: state.modeLabel,
                    icon: 'settings-gear',
                    command: 'founderOs.openSettings',
                },
                {
                    id: 'cloud-services',
                    label: 'Manage connected services',
                    description: 'GitHub, Vercel, Railway, Neon',
                    icon: 'plug',
                    command: 'founderOs.openConnections',
                },
                {
                    id: 'routing',
                    label: 'Review AI routing',
                    icon: 'pulse',
                    command: 'founderOs.recentGatewayMetadata',
                },
            ];
        case 'remote':
            return [
                {
                    id: 'remote-status',
                    label: state.connected ? 'This computer is available' : 'Sign in to enable remote control',
                    description: state.connected ? 'Founder Node connected' : undefined,
                    icon: state.connected ? 'remote' : 'lock',
                    command: state.connected ? 'founderOs.openRemoteControl' : 'founderOs.signIn',
                },
                {
                    id: 'remote-control',
                    label: 'Open web remote control',
                    icon: 'globe',
                    command: 'founderOs.openRemoteControl',
                },
                {
                    id: 'review-connection',
                    label: 'Review this computer connection',
                    icon: 'shield',
                    command: 'founderOs.manage',
                },
            ];
    }
}
//# sourceMappingURL=founder-shortcuts-state.js.map