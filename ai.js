require("dotenv").config();
const axios = require("axios");

async function askAI(prompt) {
  try {
    const res = await axios.post(
      "https://openrouter.ai/api/v1/chat/completions",
      {
        model: "anthropic/claude-3-sonnet",
        messages: [
          { role: "system", content: "You are a smart trading assistant." },
          { role: "user", content: prompt }
        ]
      },
      {
        headers: {
          "Authorization": `Bearer ${process.env.OPENROUTER_API_KEY}`,
          "Content-Type": "application/json"
        }
      }
    );

    return res.data.choices[0].message.content;

  } catch (err) {
    console.error("Error:", err.response?.data || err.message);
  }
}

// test
askAI("BTC abhi up jayega ya down?").then(console.log);