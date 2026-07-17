import { useEffect } from "react";
import { ChatView } from "./components/chat/ChatView";
import { useConversation } from "./state/conversation";

export default function App() {
  const init = useConversation((s) => s.init);

  useEffect(() => {
    void init();
  }, [init]);

  return <ChatView />;
}
