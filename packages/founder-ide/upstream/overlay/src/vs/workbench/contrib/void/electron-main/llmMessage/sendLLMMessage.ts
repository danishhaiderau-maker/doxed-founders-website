/*--------------------------------------------------------------------------------------
 *  Copyright 2025 Glass Devtools, Inc. All rights reserved.
 *  Licensed under the Apache License, Version 2.0. See LICENSE.txt for more information.
 *--------------------------------------------------------------------------------------*/

import { SendLLMMessageParams, OnText, OnFinalMessage, OnError } from '../../common/sendLLMMessageTypes.js';
import { IMetricsService } from '../../common/metricsService.js';
import { displayInfoOfProviderName } from '../../common/voidSettingsTypes.js';
import { sendLLMMessageToProviderImplementation } from './sendLLMMessage.impl.js';
// FOUNDER_OS_GATEWAY_REWIRE - Founder-managed aliases use our Gateway. Personal
// and local provider selections stay on Void's encrypted direct-provider path.
import { sendFounderOsChat, sendFounderOsFIM, founderOsEnabled } from './sendFounderOs.js';
import { headersWithoutFounderProviderProfiles, resolveFounderProviderProfile } from '../../common/founderProviderProfiles.js';
import {
	buildFounderReviewEvidencePack,
	founderReviewEvidenceMessage,
	founderReviewChatMode,
	founderReviewTools,
	isFounderIndependentReview,
	renderFounderIndependentReview,
} from './founderIndependentReview.js';


export const sendLLMMessage = async ({
	messagesType,
	messages: messages_,
	onText: onText_,
	onFinalMessage: onFinalMessage_,
	onError: onError_,
	abortRef: abortRef_,
	logging: { loggingName, loggingExtras },
	settingsOfProvider,
	modelSelection,
	modelSelectionOptions,
	overridesOfModel,
	chatMode,
	separateSystemMessage,
	mcpTools,
}: SendLLMMessageParams,

	metricsService: IMetricsService
) => {


	const { providerName, modelName } = modelSelection

	// only captures number of messages and message "shape", no actual code, instructions, prompts, etc
	const captureLLMEvent = (eventId: string, extras?: object) => {


		metricsService.capture(eventId, {
			providerName,
			modelName,
			customEndpointURL: settingsOfProvider[providerName]?.endpoint,
			numModelsAtEndpoint: settingsOfProvider[providerName]?.models?.length,
			...messagesType === 'chatMessages' ? {
				numMessages: messages_?.length,
			} : messagesType === 'FIMMessage' ? {
				prefixLength: messages_.prefix.length,
				suffixLength: messages_.suffix.length,
			} : {},
			...loggingExtras,
			...extras,
		})
	}
	const submit_time = new Date()

	let _fullTextSoFar = ''
	let _aborter: (() => void) | null = null
	let _setAborter = (fn: () => void) => { _aborter = fn }
	let _didAbort = false

	const onText: OnText = (params) => {
		const { fullText } = params
		if (_didAbort) return
		onText_(params)
		_fullTextSoFar = fullText
	}

	const onFinalMessage: OnFinalMessage = (params) => {
		const { fullText, fullReasoning, toolCall } = params
		if (_didAbort) return
		captureLLMEvent(`${loggingName} - Received Full Message`, { messageLength: fullText.length, reasoningLength: fullReasoning?.length, duration: new Date().getMilliseconds() - submit_time.getMilliseconds(), toolCallName: toolCall?.name })
		onFinalMessage_(params)
	}

	const onError: OnError = ({ message: errorMessage, fullError }) => {
		if (_didAbort) return
		console.error('sendLLMMessage onError:', errorMessage)

		// handle failed to fetch errors, which give 0 information by design
		if (errorMessage === 'TypeError: fetch failed')
			errorMessage = `Failed to fetch from ${displayInfoOfProviderName(providerName).title}. Check the endpoint in Founder Settings, or confirm that your local model provider is running.`

		captureLLMEvent(`${loggingName} - Error`, { error: errorMessage })
		onError_({ message: errorMessage, fullError })
	}

	// we should NEVER call onAbort internally, only from the outside
	const onAbort = () => {
		captureLLMEvent(`${loggingName} - Abort`, { messageLengthSoFar: _fullTextSoFar.length })
		try { _aborter?.() } // aborter sometimes automatically throws an error
		catch (e) { }
		_didAbort = true
	}
	abortRef_.current = onAbort


	if (messagesType === 'chatMessages')
		captureLLMEvent(`${loggingName} - Sending Message`, {})
	else if (messagesType === 'FIMMessage')
		captureLLMEvent(`${loggingName} - Sending FIM`, { prefixLen: messages_?.prefix?.length, suffixLen: messages_?.suffix?.length })


	// A paired Founder IDE can use Founder Managed and remembered personal/local
	// providers side by side. Only explicit founder-os-* aliases are Gateway
	// routes; every other selection must honor the provider chosen in the chat.
	const isFounderManagedSelection = modelName.startsWith('founder-os-')
	if (founderOsEnabled() && isFounderManagedSelection) {
		if (messagesType === 'chatMessages') {
			const threadId = typeof loggingExtras?.threadId === 'string' ? loggingExtras.threadId : '';
			const workspacePath = typeof loggingExtras?.workspacePath === 'string' ? loggingExtras.workspacePath : '';
			await sendFounderOsChat({
				messages: messages_, onText, onFinalMessage, onError, _setAborter,
				loggingName, modelSelection, settingsOfProvider, separateSystemMessage, chatMode, mcpTools,
				coordination: threadId && workspacePath ? { threadId, workspacePath } : undefined,
			});
			return
		}
		if (messagesType === 'FIMMessage') {
			await sendFounderOsFIM({ messages: messages_, onText, onFinalMessage, onError, _setAborter, loggingName });
			return
		}
	}

	const personalProfile = providerName === 'openAICompatible'
		? resolveFounderProviderProfile(settingsOfProvider.openAICompatible.headersJSON, modelName)
		: null;
	const isSecondBrainReview = messagesType === 'chatMessages'
		&& isFounderIndependentReview(messages_);
	const reviewWorkspacePath = typeof loggingExtras?.workspacePath === 'string'
		? loggingExtras.workspacePath
		: '';
	const reviewEvidence = isSecondBrainReview
		? founderReviewEvidenceMessage(
			reviewWorkspacePath
				? buildFounderReviewEvidencePack(reviewWorkspacePath)
				: null,
		)
		: '';
	const effectiveModelName = personalProfile?.model ?? modelName;
	const effectiveSettingsOfProvider = providerName === 'openAICompatible'
		? {
			...settingsOfProvider,
			openAICompatible: {
				...settingsOfProvider.openAICompatible,
				...(personalProfile ? {
					endpoint: personalProfile.baseUrl,
					apiKey: personalProfile.apiKey,
					headersJSON: JSON.stringify(personalProfile.headers),
				} : {
					headersJSON: headersWithoutFounderProviderProfiles(
						settingsOfProvider.openAICompatible.headersJSON,
					),
				}),
			},
		}
		: settingsOfProvider;
	const personalOnFinalMessage: OnFinalMessage = personalProfile
		? (params) => {
			const routeKind = personalProfile.kind === 'ollama' ? 'Local' : 'Personal AI';
			const latencyMs = Date.now() - submit_time.getTime();
			const review = isSecondBrainReview
				? renderFounderIndependentReview(params.fullText)
				: null;
			const receiptKind = review
				? `read-only review${review.valid ? '' : ' · unstructured'}`
				: 'direct';
			const receipt = `\n\n---\n**Founder route** | ${routeKind} | ${personalProfile.label} | ${personalProfile.model} | ${receiptKind} | outside managed quota | ${latencyMs.toLocaleString()} ms`;
			onFinalMessage({
				...params,
				fullText: `${review?.text ?? params.fullText}${receipt}`,
			});
		}
		: onFinalMessage;

try {
		const implementation = sendLLMMessageToProviderImplementation[providerName]
		if (!implementation) {
			onError({ message: `Error: Provider "${providerName}" not recognized.`, fullError: null })
			return
		}
		const { sendFIM, sendChat } = implementation
		if (messagesType === 'chatMessages') {
			const directMessages = personalProfile ? [
				{
					role: 'system' as const,
					content: [
						`You are ${personalProfile.label}, a Personal AI connected to Founder IDE.`,
						'Use the available workspace and engineering tools in the current turn when evidence is needed. Do not merely announce that you will inspect the codebase and stop.',
						'Never claim to be Founder AI or a Founder-managed model. Your configured profile and model appear in the route receipt.',
						'When the latest request begins [FOUNDER_SECOND_BRAIN_V1], act as an independent read-only reviewer: inspect evidence, do not edit files or deploy, distinguish verified defects from opinion, and return the requested verdict and concrete correction.',
					].join(' '),
				},
				...(reviewEvidence ? [{
					role: 'system' as const,
					content: reviewEvidence,
				}] : []),
				...messages_,
			] : messages_;
			await sendChat({
				messages: directMessages,
				onText,
				onFinalMessage: personalOnFinalMessage,
				onError,
				settingsOfProvider: effectiveSettingsOfProvider,
				modelSelectionOptions,
				overridesOfModel,
				modelName: effectiveModelName,
				_setAborter,
				providerName,
				separateSystemMessage,
				chatMode: founderReviewChatMode(isSecondBrainReview, chatMode),
				mcpTools: founderReviewTools(isSecondBrainReview, mcpTools),
			})
			return
		}
		if (messagesType === 'FIMMessage') {
			if (sendFIM) {
				await sendFIM({ messages: messages_, onText, onFinalMessage, onError, settingsOfProvider: effectiveSettingsOfProvider, modelSelectionOptions, overridesOfModel, modelName: effectiveModelName, _setAborter, providerName, separateSystemMessage })
				return
			}
			onError({ message: `Error running Autocomplete with ${providerName} - ${modelName}.`, fullError: null })
			return
		}
		onError({ message: `Error: Message type "${messagesType}" not recognized.`, fullError: null })
		return
	}

	catch (error) {
		if (error instanceof Error) { onError({ message: error + '', fullError: error }) }
		else { onError({ message: `Unexpected Error in sendLLMMessage: ${error}`, fullError: error }); }
		// ; (_aborter as any)?.()
		// _didAbort = true
	}



}

