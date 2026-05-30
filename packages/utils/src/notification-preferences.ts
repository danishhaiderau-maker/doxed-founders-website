export type NotificationPreferenceGroups = {
  feed: {
    buildUpdates: boolean;
    founderUpdates: boolean;
    newProjects: boolean;
    deployments: boolean;
  };
  market: {
    hotBuys: boolean;
    hotSells: boolean;
    watchlistSurges: boolean;
  };
  scoutVote: {
    newVoteOpened: boolean;
    voteEndingSoon: boolean;
    voteResult: boolean;
  };
  raiseRoom: {
    newRaise: boolean;
    milestoneReached: boolean;
    raiseClosed: boolean;
  };
  following: {
    followedFounderPosted: boolean;
    followedTraderBought: boolean;
    followedProjectUpdated: boolean;
  };
  social: {
    mentions: boolean;
    replies: boolean;
    follows: boolean;
    likes: boolean;
    helpfulMarks: boolean;
  };
  platform: {
    rewards: boolean;
    rankChanges: boolean;
    systemMessages: boolean;
  };
};

export const DEFAULT_NOTIFICATION_PREFERENCES: NotificationPreferenceGroups = {
  feed: {
    buildUpdates: true,
    founderUpdates: true,
    newProjects: true,
    deployments: true,
  },
  market: {
    hotBuys: true,
    hotSells: true,
    watchlistSurges: true,
  },
  scoutVote: {
    newVoteOpened: true,
    voteEndingSoon: true,
    voteResult: true,
  },
  raiseRoom: {
    newRaise: true,
    milestoneReached: true,
    raiseClosed: true,
  },
  following: {
    followedFounderPosted: true,
    followedTraderBought: true,
    followedProjectUpdated: true,
  },
  social: {
    mentions: true,
    replies: true,
    follows: true,
    likes: true,
    helpfulMarks: true,
  },
  platform: {
    rewards: true,
    rankChanges: true,
    systemMessages: true,
  },
};

export function mergeNotificationPreferences(
  stored: Partial<NotificationPreferenceGroups> | null | undefined,
): NotificationPreferenceGroups {
  if (!stored || typeof stored !== 'object') {
    return DEFAULT_NOTIFICATION_PREFERENCES;
  }

  const mergeGroup = <K extends keyof NotificationPreferenceGroups>(key: K) => ({
    ...DEFAULT_NOTIFICATION_PREFERENCES[key],
    ...(stored[key] ?? {}),
  });

  return {
    feed: mergeGroup('feed'),
    market: mergeGroup('market'),
    scoutVote: mergeGroup('scoutVote'),
    raiseRoom: mergeGroup('raiseRoom'),
    following: mergeGroup('following'),
    social: mergeGroup('social'),
    platform: mergeGroup('platform'),
  };
}
