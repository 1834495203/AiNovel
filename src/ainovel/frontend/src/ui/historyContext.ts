import type { CommandView, DiscussionSession, StoryView } from "../api/client";

export type OperationContext = { category: string; location: string };
export function historyContext(command: CommandView, commands: CommandView[], view: StoryView | null, sessions: DiscussionSession[]): OperationContext {
  const payload = command.submission.payload;
  if (payload.kind === 'recover_command' || payload.kind === 'undo') {
    const targetId = payload.kind === 'recover_command' ? payload.target_command_id : command.output.undone_command_id;
    const target = commands.find(item => item.submission.id === targetId);
    if (target && target.submission.id !== command.submission.id) return historyContext(target, commands.filter(item => item !== command), view, sessions);
  }
  if (payload.kind === 'discussion' || payload.kind === 'start_discussion') {
    const session = sessions.find(item => item.id === payload.session_id);
    const start = commands.find(item => item.submission.payload.kind === 'start_discussion' && item.submission.payload.session_id === payload.session_id);
    const title = session?.title || start?.output.discussion_session?.title || (start?.submission.payload.kind === 'start_discussion' ? start.submission.payload.title : '');
    return { category: '讨论会话', location: title || `会话 ${payload.session_id.slice(0, 8)}` };
  }
  const rootId = payload.kind === 'review_draft' || payload.kind === 'resolve_character' ? payload.generation_command_id : payload.kind === 'accept_rewrite' || payload.kind === 'reject_rewrite' ? payload.proposal_command_id : command.submission.id;
  const root = commands.find(item => item.submission.id === rootId) || command;
  const sceneId = 'scene_id' in payload ? payload.scene_id : command.scene_id || root.scene_id;
  const scene = view?.story.scenes.find(item => item.id === sceneId || item.source_command_id === rootId || commands.some(c => c.submission.id === item.source_command_id && c.submission.payload.kind === 'review_draft' && c.submission.payload.generation_command_id === rootId));
  const chapterId = scene?.chapter_id || ('chapter_id' in payload ? payload.chapter_id : null);
  const chapter = view?.story.chapters.find(item => item.id === chapterId);
  const position = scene ? `${chapter?.title || '章节'} / ${scene.title}` : chapter?.title;
  if (payload.kind === 'character_chat' || payload.kind === 'start_character_chat') {
    const name = view?.story.characters.find(item => item.id === payload.character_id)?.name || '角色';
    const sessionIds = [...new Set(commands.flatMap(item => item.submission.payload.kind === 'character_chat' || item.submission.payload.kind === 'start_character_chat' ? [item.submission.payload.session_id] : []))];
    return { category: '角色会话', location: `${name} · ${position || '历史阶段'} · 会话 ${sessionIds.indexOf(payload.session_id) + 1}` };
  }
  if (['generate','review_draft','resolve_character','rewrite','accept_rewrite','reject_rewrite'].includes(payload.kind)) return { category: '创作记录', location: position || `待确认草稿${root.output.writing_draft?.content.title ? ` / ${root.output.writing_draft.content.title}` : ''}` };
  return { category: payload.kind === 'interview' ? '构思访谈' : '作品操作', location: position || '整部作品' };
}
