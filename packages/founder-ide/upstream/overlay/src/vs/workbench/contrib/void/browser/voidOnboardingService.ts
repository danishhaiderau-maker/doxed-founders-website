/*--------------------------------------------------------------------------------------
 *  Copyright 2025 Glass Devtools, Inc. All rights reserved.
 *  Licensed under the Apache License, Version 2.0. See LICENSE.txt for more information.
 *--------------------------------------------------------------------------------------*/

/**
 * Founder IDE uses Founder Node pairing and the Founder OS gateway instead of
 * Void's first-run provider-key wizard. Keep this module as the overlay target,
 * but intentionally do not register Void's onboarding workbench contribution.
 *
 * Void's AI editing surfaces and provider settings remain available; only the
 * blocking startup overlay is disabled.
 */

export {};
