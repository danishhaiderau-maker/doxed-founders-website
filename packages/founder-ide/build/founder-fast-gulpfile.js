'use strict';

// Founder IDE's normal watch-client task removes the complete `out` tree
// before its first compile. This development-only task deliberately preserves
// the last known-good output, then lets VS Code's own incremental compiler
// replace changed modules as it observes them.

const path = require('path');

const checkout = process.env.FOUNDER_IDE_CHECKOUT;
if (!checkout) {
	throw new Error('FOUNDER_IDE_CHECKOUT is required');
}

process.chdir(checkout);

const gulp = require(path.join(checkout, 'node_modules', 'gulp'));
const task = require(path.join(checkout, 'build', 'lib', 'task'));
const {
	watchTask,
	watchApiProposalNamesTask,
} = require(path.join(checkout, 'build', 'lib', 'compilation'));

const founderWatchClient = task.define(
	'founder-watch-client',
	task.parallel(watchTask('out', false), watchApiProposalNamesTask),
);

gulp.task(founderWatchClient);
