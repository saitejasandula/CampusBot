import React, { useState, useEffect, useRef } from 'react';

const API_BASE = import.meta.env.VITE_API_URL || import.meta.env.VITE_API_BASE || 'http://localhost:8000';

function generateSessionId() {
  return 'session_' + Math.random().toString(36).substring(2, 10) + '_' + Date.now();
}

const SUGGESTED_QUESTIONS = [
  {
    category: 'Admissions',
    label: 'Admissions & Cutoffs',
    icon: '📋',
    question: 'What are the eligibility criteria and JEE Main cutoffs for B.Tech CSE?',
  },
  {
    category: 'Fees',
    label: 'Tuition & Hostel Fees',
    icon: '💳',
    question: 'What is the tuition fee for B.Tech and what are the hostel charges?',
  },
  {
    category: 'Exams',
    label: 'Exam & Attendance Rules',
    icon: '📝',
    question: 'What is the minimum attendance required and what are the grading rules?',
  },
  {
    category: 'Placements',
    label: 'Placements & Packages',
    icon: '💼',
    question: 'What were the highest and average placement packages for the batch of 2025?',
  },
];

export default function App() {
  const [sessionId, setSessionId] = useState(() => {
    return localStorage.getItem('campusbot_session_id') || generateSessionId();
  });
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [errorMsg, setErrorMsg] = useState(null);
  const [lastFailedQuery, setLastFailedQuery] = useState(null);
  const [feedbackGiven, setFeedbackGiven] = useState({}); // { messageId: 'up' | 'down' }
  const [escalateCardFor, setEscalateCardFor] = useState(null); // messageId
  const [escalateData, setEscalateData] = useState({ name: '', email: '', question: '' });
  const [escalateSubmitting, setEscalateSubmitting] = useState(false);
  const [escalateSuccess, setEscalateSuccess] = useState(null);

  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);

  // Save session ID
  useEffect(() => {
    localStorage.setItem('campusbot_session_id', sessionId);
  }, [sessionId]);

  // Load history on mount or session change
  useEffect(() => {
    async function fetchHistory() {
      try {
        const res = await fetch(`${API_BASE}/history/${sessionId}`);
        if (res.ok) {
          const data = await res.json();
          if (data.messages && data.messages.length > 0) {
            setMessages(data.messages);
          }
        }
      } catch (err) {
        console.warn('Could not load history:', err);
      }
    }
    fetchHistory();
  }, [sessionId]);

  // Auto scroll to bottom
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isStreaming]);

  // New Chat handler
  const handleNewChat = async () => {
    try {
      await fetch(`${API_BASE}/history/${sessionId}`, { method: 'DELETE' });
    } catch (e) {
      // Ignore
    }
    const newId = generateSessionId();
    setSessionId(newId);
    setMessages([]);
    setErrorMsg(null);
    setFeedbackGiven({});
    setEscalateCardFor(null);
    setEscalateSuccess(null);
  };

  // Submit message & handle SSE stream
  const sendMessage = async (textToSend) => {
    const query = (textToSend || input).trim();
    if (!query || isStreaming) return;

    setErrorMsg(null);
    setLastFailedQuery(null);
    setInput('');

    // Add user message to UI immediately
    const userMsgId = 'user_' + Date.now();
    const botMsgPlaceholderId = 'bot_' + Date.now();

    const updatedMessages = [
      ...messages,
      { id: userMsgId, role: 'user', content: query, timestamp: new Date().toISOString() },
      { id: botMsgPlaceholderId, role: 'assistant', content: '', sources: [], timestamp: new Date().toISOString(), isPending: true }
    ];

    setMessages(updatedMessages);
    setIsStreaming(true);

    try {
      const response = await fetch(`${API_BASE}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, message: query }),
      });

      if (!response.ok) {
        throw new Error(`Server returned HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let botContent = '';
      let botSources = [];
      let finalMessageId = botMsgPlaceholderId;
      let triggeredFallback = false;
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split('\n\n');
        // Keep trailing incomplete fragment in buffer
        buffer = events.pop() || '';

        for (const ev of events) {
          if (!ev.trim()) continue;
          const lines = ev.split('\n');
          let eventType = 'message';
          let dataStr = '';

          for (const line of lines) {
            if (line.startsWith(':')) {
              // SSE comment / heartbeat from Render proxy
              continue;
            }
            if (line.startsWith('event:')) {
              eventType = line.replace('event:', '').trim();
            } else if (line.startsWith('data:')) {
              dataStr = line.replace('data:', '').trim();
            }
          }

          if (eventType === 'token' && dataStr) {
            try {
              const parsed = JSON.parse(dataStr);
              botContent += parsed.token;
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === botMsgPlaceholderId
                    ? { ...m, content: botContent, isPending: false }
                    : m
                )
              );
            } catch (e) {
              console.warn('Error parsing token SSE:', e);
            }
          } else if (eventType === 'final' && dataStr) {
            try {
              const parsed = JSON.parse(dataStr);
              botSources = parsed.sources || [];
              triggeredFallback = !!parsed.is_fallback;
              if (parsed.message_id) {
                finalMessageId = parsed.message_id;
              }
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === botMsgPlaceholderId
                    ? {
                        ...m,
                        id: finalMessageId,
                        content: botContent,
                        sources: botSources,
                        is_fallback: triggeredFallback,
                        isPending: false,
                      }
                    : m
                )
              );

              if (triggeredFallback) {
                setEscalateCardFor(finalMessageId);
                setEscalateData((prev) => ({ ...prev, question: query }));
              }
            } catch (e) {
              console.warn('Error parsing final SSE:', e);
            }
          }
        }
      }
    } catch (err) {
      console.error('Chat stream error:', err);
      setErrorMsg('Unable to connect to the helpdesk server. Please ensure the backend is running.');
      setLastFailedQuery(query);
      // Remove empty pending placeholder if failed
      setMessages((prev) => prev.filter((m) => m.id !== botMsgPlaceholderId));
    } finally {
      setIsStreaming(false);
      inputRef.current?.focus();
    }
  };

  // Feedback handler
  const handleFeedback = async (messageId, helpful) => {
    try {
      const res = await fetch(`${API_BASE}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message_id: messageId, helpful }),
      });
      if (res.ok) {
        setFeedbackGiven((prev) => ({
          ...prev,
          [messageId]: helpful ? 'up' : 'down',
        }));
      }
    } catch (e) {
      console.warn('Feedback submission failed:', e);
    }
  };

  // Escalation form submit
  const handleEscalateSubmit = async (e) => {
    e.preventDefault();
    if (!escalateData.name || !escalateData.email || !escalateData.question) return;

    setEscalateSubmitting(true);
    try {
      const res = await fetch(`${API_BASE}/escalate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          name: escalateData.name,
          email: escalateData.email,
          question: escalateData.question,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setEscalateSuccess(data.ticket_id);
      } else {
        alert('Failed to submit escalation ticket. Please try again.');
      }
    } catch (err) {
      alert('Error connecting to ticket service.');
    } finally {
      setEscalateSubmitting(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div className="app-container">
      {/* Top Header */}
      <header className="header">
        <div className="header-left">
          <div className="logo-badge">🎓</div>
          <div>
            <h1 className="header-title">CampusBot</h1>
            <p className="header-subtitle">Greenfield Institute of Technology (GIT)</p>
          </div>
        </div>
        <div className="header-right">
          <div className="status-indicator">
            <span className="status-dot"></span>
            <span className="status-text">Helpdesk Online</span>
          </div>
          <button className="new-chat-btn" onClick={handleNewChat} title="Start fresh conversation">
            <span className="btn-icon">↺</span> New Chat
          </button>
        </div>
      </header>

      {/* Main Chat Area */}
      <main className="chat-window">
        {messages.length === 0 ? (
          <div className="empty-state">
            <div className="empty-badge">🏫 Greenfield Helpdesk</div>
            <h2 className="empty-title">Welcome to CampusBot</h2>
            <p className="empty-desc">
              Your instant guide to admissions, academic regulations, tuition & hostel fees, semester exams, and placements at GIT.
            </p>

            <div className="suggested-grid">
              {SUGGESTED_QUESTIONS.map((item, idx) => (
                <button
                  key={idx}
                  className="suggested-card"
                  onClick={() => sendMessage(item.question)}
                >
                  <span className="suggested-icon">{item.icon}</span>
                  <div className="suggested-text">
                    <span className="suggested-label">{item.label}</span>
                    <span className="suggested-q">{item.question}</span>
                  </div>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="messages-list">
            {messages.map((m) => {
              const isUser = m.role === 'user';
              const isFallback = m.is_fallback;
              const feedback = feedbackGiven[m.id];

              return (
                <div key={m.id} className={`message-row ${isUser ? 'user-row' : 'bot-row'}`}>
                  <div className={`message-avatar ${isUser ? 'user-avatar' : 'bot-avatar'}`}>
                    {isUser ? '👤' : '🎓'}
                  </div>

                  <div className={`message-bubble ${isUser ? 'user-bubble' : 'bot-bubble'}`}>
                    {m.isPending && !m.content ? (
                      <div className="typing-dots">
                        <span></span>
                        <span></span>
                        <span></span>
                      </div>
                    ) : (
                      <div className="message-content">{m.content}</div>
                    )}

                    {/* Sources Chips */}
                    {!isUser && m.sources && m.sources.length > 0 && (
                      <div className="sources-container">
                        <span className="sources-label">Sources:</span>
                        {m.sources.map((src, i) => (
                          <span key={i} className="source-chip" title={`Retrieved from ${src}`}>
                            📄 {src}
                          </span>
                        ))}
                      </div>
                    )}

                    {/* Feedback & Actions for Bot Messages */}
                    {!isUser && !m.isPending && m.content && (
                      <div className="bot-actions">
                        <div className="feedback-buttons">
                          <button
                            className={`feedback-btn ${feedback === 'up' ? 'active-up' : ''}`}
                            onClick={() => handleFeedback(m.id, true)}
                            title="Helpful"
                          >
                            👍
                          </button>
                          <button
                            className={`feedback-btn ${feedback === 'down' ? 'active-down' : ''}`}
                            onClick={() => handleFeedback(m.id, false)}
                            title="Not helpful"
                          >
                            👎
                          </button>
                          {feedback && (
                            <span className="feedback-ack">
                              {feedback === 'up' ? 'Thanks for the feedback!' : 'Feedback noted.'}
                            </span>
                          )}
                        </div>

                        {!isFallback && (
                          <button
                            className="escalate-link-btn"
                            onClick={() => {
                              setEscalateCardFor(m.id);
                              setEscalateData((prev) => ({
                                ...prev,
                                question: m.content ? 'Regarding: ' + m.content.slice(0, 80) + '...' : '',
                              }));
                            }}
                          >
                            Talk to a human ↗
                          </button>
                        )}
                      </div>
                    )}

                    {/* Human Escalation Form Card */}
                    {!isUser && escalateCardFor === m.id && (
                      <div className="escalate-card">
                        <div className="escalate-header">
                          <span className="escalate-title">👨‍💼 Campus Human Helpdesk Ticket</span>
                          <button
                            className="escalate-close-btn"
                            onClick={() => {
                              setEscalateCardFor(null);
                              setEscalateSuccess(null);
                            }}
                          >
                            ✕
                          </button>
                        </div>

                        {escalateSuccess ? (
                          <div className="escalate-success-box">
                            <div className="ticket-badge">Ticket Created: {escalateSuccess}</div>
                            <p>
                              A student advisor has received your request. We will follow up via email at{' '}
                              <strong>{escalateData.email}</strong> within 1 business day.
                            </p>
                          </div>
                        ) : (
                          <form onSubmit={handleEscalateSubmit} className="escalate-form">
                            <p className="escalate-desc">
                              Please provide your contact information so an advisor can review your case:
                            </p>
                            <div className="form-group">
                              <label>Full Name</label>
                              <input
                                type="text"
                                required
                                placeholder="Student Name"
                                value={escalateData.name}
                                onChange={(e) =>
                                  setEscalateData({ ...escalateData, name: e.target.value })
                                }
                              />
                            </div>
                            <div className="form-group">
                              <label>Email Address</label>
                              <input
                                type="email"
                                required
                                placeholder="name@domain.edu"
                                value={escalateData.email}
                                onChange={(e) =>
                                  setEscalateData({ ...escalateData, email: e.target.value })
                                }
                              />
                            </div>
                            <div className="form-group">
                              <label>Question / Concern</label>
                              <textarea
                                rows={2}
                                required
                                value={escalateData.question}
                                onChange={(e) =>
                                  setEscalateData({ ...escalateData, question: e.target.value })
                                }
                              />
                            </div>
                            <button
                              type="submit"
                              className="escalate-submit-btn"
                              disabled={escalateSubmitting}
                            >
                              {escalateSubmitting ? 'Submitting Ticket...' : 'Submit Support Ticket'}
                            </button>
                          </form>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
            <div ref={messagesEndRef} />
          </div>
        )}

        {/* Error Alert with Retry */}
        {errorMsg && (
          <div className="error-banner">
            <span className="error-icon">⚠️</span>
            <div className="error-text">
              <span>{errorMsg}</span>
              {lastFailedQuery && (
                <button
                  className="retry-btn"
                  onClick={() => sendMessage(lastFailedQuery)}
                >
                  Retry
                </button>
              )}
            </div>
          </div>
        )}
      </main>

      {/* Input Form Footer */}
      <footer className="footer">
        <div className="input-container">
          <textarea
            ref={inputRef}
            className="chat-textarea"
            placeholder="Ask about admissions, fees, exams, placements, hostel..."
            rows={1}
            value={input}
            disabled={isStreaming}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <button
            className="send-button"
            onClick={() => sendMessage()}
            disabled={isStreaming || !input.trim()}
            title="Send (Enter)"
          >
            {isStreaming ? (
              <span className="send-spinner"></span>
            ) : (
              <span className="send-arrow">➤</span>
            )}
          </button>
        </div>
        <div className="footer-disclaimer">
          CampusBot provides answers strictly based on official GIT institutional records. Press Enter to send, Shift+Enter for newline.
        </div>
      </footer>
    </div>
  );
}
